from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.baseline import BaselineStatus, BaselineTask
from src.schemas.baseline import BaselineTaskCreate
from src.services import baseline_collector
from src.services.asset_access import (
    AssetAuthorizationError, all_assets_visible, evidence_asset_ids,
    require_background_assets,
)
from src.services.crypto import CredentialVault

TERMINAL = {BaselineStatus.COMPLETED, BaselineStatus.FAILED, BaselineStatus.CANCELLED}
TRANSITIONS = {
    BaselineStatus.QUEUED: {BaselineStatus.SCANNING, BaselineStatus.CANCELLED},
    BaselineStatus.SCANNING: {BaselineStatus.AGGREGATING, BaselineStatus.FAILED, BaselineStatus.CANCELLED},
    BaselineStatus.AGGREGATING: {BaselineStatus.COMPLETED, BaselineStatus.FAILED, BaselineStatus.CANCELLED},
}


class BaselineError(ValueError):
    pass


def list_tasks(
    session: Session, visible_asset_ids: set[str] | None = None
) -> list[BaselineTask]:
    tasks = list(session.scalars(select(BaselineTask).order_by(BaselineTask.created_at.desc())))
    if visible_asset_ids is None:
        return tasks
    return [
        task for task in tasks
        if all_assets_visible(task_asset_ids(task), visible_asset_ids)
    ]


def task_asset_ids(task: BaselineTask) -> set[str]:
    return evidence_asset_ids(
        task.asset_ids or [], task.asset_snapshot or [], task.results or [],
    )


def create_task(session: Session, payload: BaselineTaskCreate, rerun_of_id: str | None = None) -> BaselineTask:
    assets = list(session.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))))
    if len(assets) != len(set(payload.asset_ids)):
        raise BaselineError("部分检查资产不存在")
    if any(asset.lifecycle_status != AssetLifecycleStatus.ENABLED for asset in assets):
        raise BaselineError("已停用或归档资产不能执行基线检查")
    ordered = {asset.id: asset for asset in assets}
    now = datetime.now(timezone.utc)
    task = BaselineTask(
        id=str(uuid4()), name=payload.name.strip(), status=BaselineStatus.QUEUED, progress=0,
        read_only=True, policy_id=baseline_collector.POLICY_ID,
        policy_version=baseline_collector.POLICY_VERSION, asset_ids=payload.asset_ids,
        asset_snapshot=[_snapshot(ordered[item]) for item in payload.asset_ids], results=[],
        rerun_of_id=rerun_of_id, execution_log=[_log(BaselineStatus.QUEUED, "任务已进入只读检查队列", now)],
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def start_task(session: Session, task: BaselineTask) -> BaselineTask:
    if task.status != BaselineStatus.QUEUED:
        raise BaselineError("仅排队中的任务可以启动")
    enabled = set(session.scalars(select(Asset.id).where(Asset.id.in_(task.asset_ids), Asset.lifecycle_status == AssetLifecycleStatus.ENABLED)))
    if enabled != set(task.asset_ids):
        raise BaselineError("任务包含已停用或归档资产")
    return _transition(session, task, BaselineStatus.SCANNING, 10, "正在执行 SSH 只读检查")


def cancel_task(session: Session, task: BaselineTask) -> BaselineTask:
    if task.status in TERMINAL:
        raise BaselineError("终态任务不能取消")
    return _transition(session, task, BaselineStatus.CANCELLED, task.progress, "任务已由操作员取消")


def rerun_task(session: Session, task: BaselineTask) -> BaselineTask:
    if task.status not in TERMINAL:
        raise BaselineError("仅终态任务可以重跑")
    return create_task(session, BaselineTaskCreate(name=f"{task.name} - 重跑", asset_ids=task.asset_ids), task.id)


def execute_task(
    factory: sessionmaker[Session], task_id: str, vault: CredentialVault,
    operator_user_id: str | None = None, operator: str | None = None,
) -> None:
    operator_name = operator or "scheduler"
    with factory() as session:
        task = session.get(BaselineTask, task_id)
        if task is None or task.status != BaselineStatus.SCANNING:
            return
        results: list[dict[str, object]] = []
        try:
            require_background_assets(session, operator_name, operator_user_id, task.asset_ids)
            for asset_id in task.asset_ids:
                require_background_assets(
                    session, operator_name, operator_user_id, task.asset_ids
                )
                asset = session.get(Asset, asset_id)
                if asset is None:
                    results.append(_failure(asset_id, "未知资产", "asset_missing", "资产已不存在"))
                    continue
                if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
                    state = asset.lifecycle_status.value
                    results.append(_failure(asset.id, asset.name, f"asset_{state}", f"资产当前状态为 {state}，禁止 SSH 采集", asset))
                    continue
                if asset.credential is None:
                    results.append(_failure(asset.id, asset.name, "credential_missing", "未配置 SSH 凭据", asset))
                    continue
                try:
                    credential = asset.credential
                    secret = vault.decrypt(credential.encrypted_secret, credential.nonce, credential.id, credential.credential_type.value, credential.key_version)
                    checks = baseline_collector.collect(asset, credential, secret)
                    require_background_assets(
                        session, operator_name, operator_user_id, task.asset_ids
                    )
                    results.append({"asset_id": asset.id, "asset_name": asset.name, "status": "checked", "checks": checks})
                except AssetAuthorizationError:
                    raise
                except Exception as exc:
                    results.append(_failure(asset.id, asset.name, type(exc).__name__, str(exc)[:500], asset))
            require_background_assets(session, operator_name, operator_user_id, task.asset_ids)
            task.results = results
            _transition(session, task, BaselineStatus.AGGREGATING, 85, "正在汇总合规结果")
            checks = [check for result in results for check in result["checks"]]
            passed = sum(check["status"] == "passed" for check in checks)
            task.summary = {"assets_total": len(task.asset_ids), "assets_checked": sum(result["status"] == "checked" for result in results), "passed": passed, "failed": len(checks) - passed, "compliance_rate": round(passed * 100 / len(checks), 1) if checks else 0}
            final = BaselineStatus.COMPLETED if checks else BaselineStatus.FAILED
            task.error_message = None if checks else next(
                (str(result.get("message", "")) for result in results if result.get("message")),
                "没有资产完成有效检查",
            )
            _transition(session, task, final, 100, "基线检查完成" if checks else "基线检查失败")
        except AssetAuthorizationError as exc:
            if task.status in {BaselineStatus.SCANNING, BaselineStatus.AGGREGATING}:
                task.error_message = str(exc)
                _transition(session, task, BaselineStatus.FAILED, task.progress, "基线检查因授权失效而停止")
        except Exception as exc:
            task.error_message = str(exc)[:1000]
            _transition(session, task, BaselineStatus.FAILED, task.progress, "基线检查执行失败")


def _failure(
    asset_id: str, asset_name: str, error_code: str, message: str,
    asset: Asset | None = None,
) -> dict[str, object]:
    return {
        "asset_id": asset_id, "asset_name": asset_name, "status": "failed",
        "checks": [], "error_code": error_code, "message": message,
        "asset_snapshot": _snapshot(asset) if asset else None,
        "evidence": {
            "collection_mode": "fixed_read_only_ssh",
            "host_key_policy": "auto_accept_memory_only_v1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _transition(session: Session, task: BaselineTask, status: BaselineStatus, progress: int, message: str) -> BaselineTask:
    if status not in TRANSITIONS.get(task.status, set()):
        raise BaselineError(f"非法状态迁移：{task.status.value} -> {status.value}")
    now = datetime.now(timezone.utc)
    task.status, task.progress = status, progress
    if status == BaselineStatus.SCANNING:
        task.started_at = now
    if status in TERMINAL:
        task.finished_at = now
    task.execution_log = [*task.execution_log, _log(status, message, now)]
    session.commit()
    session.refresh(task)
    return task


def _snapshot(asset: Asset) -> dict[str, object]:
    return {"id": asset.id, "name": asset.name, "host": asset.host, "os_family": asset.os_family.value}


def _log(status: BaselineStatus, message: str, occurred_at: datetime) -> dict[str, object]:
    return {"status": status.value, "message": message, "occurred_at": occurred_at.isoformat()}
