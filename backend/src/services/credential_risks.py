from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.credential import Credential, CredentialType
from src.models.credential_risk import CredentialRiskStatus, CredentialRiskTask
from src.schemas.credential_risk import CredentialRiskTaskCreate
from src.services import credential_risk_policy
from src.services.crypto import CredentialVault

TERMINAL = {CredentialRiskStatus.COMPLETED, CredentialRiskStatus.FAILED, CredentialRiskStatus.CANCELLED}
TRANSITIONS = {
    CredentialRiskStatus.QUEUED: {CredentialRiskStatus.ASSESSING, CredentialRiskStatus.CANCELLED},
    CredentialRiskStatus.ASSESSING: {CredentialRiskStatus.AGGREGATING, CredentialRiskStatus.FAILED, CredentialRiskStatus.CANCELLED},
    CredentialRiskStatus.AGGREGATING: {CredentialRiskStatus.COMPLETED, CredentialRiskStatus.FAILED, CredentialRiskStatus.CANCELLED},
}


class CredentialRiskError(ValueError):
    pass


def list_tasks(session: Session) -> list[CredentialRiskTask]:
    return list(session.scalars(select(CredentialRiskTask).order_by(CredentialRiskTask.created_at.desc())))


def create_task(session: Session, payload: CredentialRiskTaskCreate, rerun_of_id: str | None = None) -> CredentialRiskTask:
    credentials = list(session.scalars(select(Credential).where(Credential.id.in_(payload.credential_ids))))
    if len(credentials) != len(set(payload.credential_ids)):
        raise CredentialRiskError("部分凭据不存在")
    if any(item.credential_type != CredentialType.PASSWORD for item in credentials):
        raise CredentialRiskError("仅支持密码型凭据的本地策略评估")
    ordered = {item.id: item for item in credentials}
    now = datetime.now(timezone.utc)
    task = CredentialRiskTask(
        id=str(uuid4()), name=payload.name.strip(), status=CredentialRiskStatus.QUEUED,
        progress=0, offline_only=True, policy_version=credential_risk_policy.POLICY_VERSION,
        credential_ids=payload.credential_ids, credential_snapshot=[_snapshot(ordered[item]) for item in payload.credential_ids],
        results=[], rerun_of_id=rerun_of_id,
        execution_log=[_log(CredentialRiskStatus.QUEUED, "任务已进入离线评估队列", now)],
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def start_task(session: Session, task: CredentialRiskTask) -> CredentialRiskTask:
    if task.status != CredentialRiskStatus.QUEUED:
        raise CredentialRiskError("仅排队中的任务可以启动")
    return _transition(session, task, CredentialRiskStatus.ASSESSING, 10, "正在后端内存中执行离线策略评估")


def cancel_task(session: Session, task: CredentialRiskTask) -> CredentialRiskTask:
    if task.status in TERMINAL:
        raise CredentialRiskError("终态任务不能取消")
    return _transition(session, task, CredentialRiskStatus.CANCELLED, task.progress, "任务已由操作员取消")


def rerun_task(session: Session, task: CredentialRiskTask) -> CredentialRiskTask:
    if task.status not in TERMINAL:
        raise CredentialRiskError("仅终态任务可以重跑")
    return create_task(session, CredentialRiskTaskCreate(name=f"{task.name} - 重跑", credential_ids=task.credential_ids), task.id)


def execute_task(factory: sessionmaker[Session], task_id: str, vault: CredentialVault) -> None:
    with factory() as session:
        task = session.get(CredentialRiskTask, task_id)
        if task is None or task.status != CredentialRiskStatus.ASSESSING:
            return
        try:
            results = [_assess_credential(session, vault, credential_id) for credential_id in task.credential_ids]
            task.results = results
            _transition(session, task, CredentialRiskStatus.AGGREGATING, 85, "正在汇总风险结论")
            task.summary = {
                "credentials_total": len(results),
                "at_risk": sum(item["status"] == "at_risk" for item in results),
                "high": sum(item["risk_level"] == "high" for item in results),
                "medium": sum(item["risk_level"] == "medium" for item in results),
                "passed": sum(item["status"] == "passed" for item in results),
            }
            _transition(session, task, CredentialRiskStatus.COMPLETED, 100, "弱口令风险评估完成")
        except Exception as exc:
            task.error_message = type(exc).__name__
            _transition(session, task, CredentialRiskStatus.FAILED, task.progress, "弱口令风险评估失败")


def _assess_credential(session: Session, vault: CredentialVault, credential_id: str) -> dict[str, object]:
    credential = session.get(Credential, credential_id)
    if credential is None or credential.credential_type != CredentialType.PASSWORD:
        raise CredentialRiskError("凭据不存在或类型已变更")
    secret = vault.decrypt(credential.encrypted_secret, credential.nonce, credential.id, credential.credential_type.value, credential.key_version)
    assessment = credential_risk_policy.assess(secret, credential.username)
    return {"credential_id": credential.id, "credential_name": credential.name, "username": credential.username, **assessment}


def _snapshot(item: Credential) -> dict[str, object]:
    return {"id": item.id, "name": item.name, "username": item.username, "asset_count": item.asset_count}


def _transition(session: Session, task: CredentialRiskTask, status: CredentialRiskStatus, progress: int, message: str) -> CredentialRiskTask:
    if status not in TRANSITIONS.get(task.status, set()):
        raise CredentialRiskError(f"非法状态迁移：{task.status.value} -> {status.value}")
    now = datetime.now(timezone.utc)
    task.status, task.progress = status, progress
    if status == CredentialRiskStatus.ASSESSING:
        task.started_at = now
    if status in TERMINAL:
        task.finished_at = now
    task.execution_log = [*task.execution_log, _log(status, message, now)]
    session.commit()
    session.refresh(task)
    return task


def _log(status: CredentialRiskStatus, message: str, occurred_at: datetime) -> dict[str, object]:
    return {"status": status.value, "message": message, "occurred_at": occurred_at.isoformat()}
