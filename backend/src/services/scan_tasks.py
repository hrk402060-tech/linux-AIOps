from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.credential import Credential
from src.models.knowledge import KnowledgeAdvisory, KnowledgeRelease
from src.models.scan_task import ScanStatus, ScanTask
from src.schemas.scan_task import ScanTaskCreate
from src.services import scan_engine
from src.services import vulnerabilities
from src.services.asset_access import (
    AssetAuthorizationError, all_assets_visible, evidence_asset_ids,
    require_background_assets,
)
from src.services.crypto import CredentialVault
from src.services.scan_task_lifecycle import (
    ScanTaskConflictError, is_removed, lock_for_write, lock_task_row, not_removed,
)

TERMINAL_STATUSES = {ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED}
ALLOWED_TRANSITIONS = {
    ScanStatus.QUEUED: {ScanStatus.SCANNING, ScanStatus.CANCELLED},
    ScanStatus.SCANNING: {ScanStatus.AGGREGATING, ScanStatus.FAILED, ScanStatus.CANCELLED},
    ScanStatus.AGGREGATING: {ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED},
}


class ScanTaskValidationError(ValueError):
    pass


class ScanTaskAccessError(PermissionError):
    pass


def list_tasks(
    session: Session, archive_view: str = "active",
    visible_asset_ids: set[str] | None = None,
) -> list[ScanTask]:
    statement = select(ScanTask).where(not_removed()).order_by(ScanTask.created_at.desc())
    if archive_view == "active":
        statement = statement.where(ScanTask.archived.is_(False))
    elif archive_view == "archived":
        statement = statement.where(ScanTask.archived.is_(True))
    elif archive_view != "all":
        raise ScanTaskValidationError("无效的扫描归档筛选")
    items = list(session.scalars(statement))
    return [
        item for item in items
        if task_assets_visible(item, visible_asset_ids)
    ]


def get_task(session: Session, task_id: str) -> ScanTask | None:
    return session.get(ScanTask, task_id)


def task_assets_visible(task: ScanTask, visible_asset_ids: set[str] | None) -> bool:
    return all_assets_visible(evidence_asset_ids(
        task.asset_ids or [], task.asset_snapshot or [], task.findings or [],
    ), visible_asset_ids)


def ensure_task_assets_visible(
    task: ScanTask, visible_asset_ids: set[str] | None
) -> None:
    if not task_assets_visible(task, visible_asset_ids):
        raise ScanTaskAccessError("当前账号未获该扫描任务全部资产的访问授权")


def ensure_asset_ids_visible(
    asset_ids: list[str], visible_asset_ids: set[str] | None
) -> None:
    if not all_assets_visible(asset_ids, visible_asset_ids):
        raise ScanTaskAccessError("当前账号未获全部扫描资产的访问授权")


def create_task(
    session: Session, payload: ScanTaskCreate, rerun_of_id: str | None = None,
    schedule_id: str | None = None, visible_asset_ids: set[str] | None = None,
) -> ScanTask:
    ensure_asset_ids_visible(payload.asset_ids, visible_asset_ids)
    assets = list(session.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))))
    found = {asset.id for asset in assets}
    missing = [asset_id for asset_id in payload.asset_ids if asset_id not in found]
    if missing:
        raise ScanTaskValidationError("部分扫描资产不存在")
    unavailable = [
        asset.name for asset in assets
        if asset.lifecycle_status != AssetLifecycleStatus.ENABLED
    ]
    if unavailable:
        raise ScanTaskValidationError("已停用或归档资产不能创建扫描任务")
    ordered = {asset.id: asset for asset in assets}
    active = session.scalar(
        select(KnowledgeRelease).where(KnowledgeRelease.status == "active")
    )
    now = datetime.now(timezone.utc)
    task = ScanTask(
        id=str(uuid4()), name=payload.name, scan_type=payload.scan_type,
        status=ScanStatus.QUEUED, progress=0, read_only=True,
        asset_ids=payload.asset_ids,
        asset_snapshot=[_asset_snapshot(ordered[item]) for item in payload.asset_ids],
        knowledge_release_id=active.id if active else None,
        knowledge_version=active.version if active else None, rerun_of_id=rerun_of_id,
        schedule_id=schedule_id,
        findings=[],
        execution_log=[_log_entry(ScanStatus.QUEUED, "任务已进入只读扫描队列", now)],
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def start_task(
    session: Session, task: ScanTask, visible_asset_ids: set[str] | None = None
) -> ScanTask:
    ensure_task_assets_visible(task, visible_asset_ids)
    task = lock_for_write(session, task)
    if task.archived:
        raise ScanTaskConflictError("已归档任务不能启动")
    if task.status != ScanStatus.QUEUED:
        raise ScanTaskConflictError("仅排队中的任务可以启动")
    enabled_ids = set(session.scalars(select(Asset.id).where(
        Asset.id.in_(task.asset_ids),
        Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
    )))
    if enabled_ids != set(task.asset_ids):
        raise ScanTaskConflictError("任务包含已停用或归档资产，不能启动")
    _transition(session, task, ScanStatus.SCANNING, 10, "正在执行只读资产采集")
    return task


def execute_task(
    session_factory: sessionmaker[Session], task_id: str, vault: CredentialVault,
    operator_user_id: str | None = None, operator: str | None = None,
) -> None:
    operator_name = operator or "scheduler"
    with session_factory() as session:
        task = get_task(session, task_id)
        if task is None or task.status != ScanStatus.SCANNING:
            return
        try:
            require_background_assets(session, operator_name, operator_user_id, task.asset_ids)
            results, findings = _collect_targets(
                session, task, vault, operator_user_id, operator_name
            )
            require_background_assets(session, operator_name, operator_user_id, task.asset_ids)
            task = lock_task_row(session, task.id)
            if is_removed(session, task.id) or task.status != ScanStatus.SCANNING:
                return
            _transition(
                session, task, ScanStatus.AGGREGATING, 85,
                "正在按厂商公告优先规则汇总",
            )
            summary = _build_summary(task, results, findings)
            task = lock_task_row(session, task.id)
            if is_removed(session, task.id) or task.status != ScanStatus.AGGREGATING:
                return
            task.result_summary = summary
            task.findings = findings
            task.report_content = _build_report(task, summary)
            if int(summary["assets_scanned"]) == 0:
                task.error_message = "没有资产完成只读采集"
                _transition(session, task, ScanStatus.FAILED, 100, "扫描失败，未完成有效采集")
            else:
                vulnerabilities.persist_findings(session, task, results)
                _transition(session, task, ScanStatus.COMPLETED, 100, "扫描完成，报告已生成")
        except AssetAuthorizationError as exc:
            task = lock_task_row(session, task.id)
            if not is_removed(session, task.id) and task.status in {
                ScanStatus.SCANNING, ScanStatus.AGGREGATING,
            }:
                task.error_message = str(exc)
                _transition(session, task, ScanStatus.FAILED, task.progress, "扫描因授权失效而停止")
        except Exception as exc:
            session.rollback()
            task = lock_task_row(session, task.id)
            if is_removed(session, task.id):
                return
            if task.status in {ScanStatus.SCANNING, ScanStatus.AGGREGATING}:
                task.error_message = str(exc)[:1000]
                _transition(session, task, ScanStatus.FAILED, task.progress, "扫描执行失败")


def cancel_task(
    session: Session, task: ScanTask, visible_asset_ids: set[str] | None = None
) -> ScanTask:
    ensure_task_assets_visible(task, visible_asset_ids)
    task = lock_for_write(session, task)
    if task.status in TERMINAL_STATUSES:
        raise ScanTaskConflictError("终态任务不能取消")
    _transition(session, task, ScanStatus.CANCELLED, task.progress, "任务已由操作员取消")
    return task


def rerun_task(
    session: Session, task: ScanTask, visible_asset_ids: set[str] | None = None
) -> ScanTask:
    ensure_task_assets_visible(task, visible_asset_ids)
    task = lock_for_write(session, task)
    if task.status not in TERMINAL_STATUSES:
        raise ScanTaskConflictError("仅完成、失败或取消的任务可以重跑")
    payload = ScanTaskCreate(
        name=f"{task.name} - 重跑", scan_type=task.scan_type, asset_ids=task.asset_ids
    )
    return create_task(
        session, payload, rerun_of_id=task.id, visible_asset_ids=visible_asset_ids
    )


def set_archived(
    session: Session, task: ScanTask, archived: bool, operator: str,
    visible_asset_ids: set[str] | None = None,
) -> ScanTask:
    ensure_task_assets_visible(task, visible_asset_ids)
    task = lock_for_write(session, task)
    if task.status not in TERMINAL_STATUSES:
        raise ScanTaskConflictError("仅完成、失败或取消的任务可以归档")
    task.archived = archived
    task.archived_at = datetime.now(timezone.utc) if archived else None
    task.archived_by = operator if archived else None
    session.commit()
    session.refresh(task)
    return task


def _collect_targets(
    session: Session, task: ScanTask, vault: CredentialVault,
    operator_user_id: str | None = None, operator: str = "scheduler",
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    advisories = _task_advisories(session, task)
    results: list[dict[str, object]] = []
    findings: list[dict[str, object]] = []
    for index, asset_id in enumerate(task.asset_ids):
        session.refresh(task)
        if task.status != ScanStatus.SCANNING or is_removed(session, task.id):
            break
        require_background_assets(
            session, operator, operator_user_id, task.asset_ids
        )
        result = _collect_asset(session, session.get(Asset, asset_id), advisories, task, vault)
        require_background_assets(
            session, operator, operator_user_id, task.asset_ids
        )
        task = lock_task_row(session, task.id)
        if is_removed(session, task.id) or task.status != ScanStatus.SCANNING:
            break
        results.append(result)
        for finding in result.get("findings", []):
            findings.append({**finding, "asset_id": asset_id, "asset_name": result["asset_name"]})
        task.progress = 10 + round(65 * (index + 1) / len(task.asset_ids))
        session.commit()
    return results, findings


def _collect_asset(
    session: Session, asset: Asset | None, advisories: list[KnowledgeAdvisory],
    task: ScanTask, vault: CredentialVault,
) -> dict[str, object]:
    if asset is None:
        return {"asset_name": "已删除资产", "status": "failed", "message": "资产已不存在"}
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        state = asset.lifecycle_status.value
        return {
            "asset_id": asset.id, "asset_name": asset.name, "status": "failed",
            "message": f"资产当前状态为 {state}，禁止 SSH 采集",
            "error_code": f"asset_{state}",
        }
    if asset.credential_id is None:
        return {"asset_name": asset.name, "status": "failed", "message": "未配置 SSH 凭据"}
    credential = session.get(Credential, asset.credential_id)
    if credential is None:
        return {"asset_name": asset.name, "status": "failed", "message": "SSH 凭据已不存在"}
    try:
        secret = vault.decrypt(
            credential.encrypted_secret, credential.nonce, credential.id,
            credential.credential_type.value, credential.key_version,
        )
        result = scan_engine.assess_asset(
            asset, credential, secret, advisories, task.scan_type
        )
        credential.last_used_at = datetime.now(timezone.utc)
        session.commit()
        return result
    except (scan_engine.ScanCollectionError, ValueError) as exc:
        return {"asset_name": asset.name, "status": "failed", "message": str(exc)}


def _task_advisories(session: Session, task: ScanTask) -> list[KnowledgeAdvisory]:
    if task.knowledge_release_id is None:
        return []
    statement = select(KnowledgeAdvisory).where(
        KnowledgeAdvisory.release_id == task.knowledge_release_id
    )
    return list(session.scalars(statement))


def _transition(
    session: Session, task: ScanTask, status: ScanStatus, progress: int, message: str
) -> None:
    if status not in ALLOWED_TRANSITIONS.get(task.status, set()):
        raise ScanTaskConflictError(f"非法状态迁移：{task.status.value} -> {status.value}")
    now = datetime.now(timezone.utc)
    task.status = status
    task.progress = progress
    if status == ScanStatus.SCANNING:
        task.started_at = now
    if status in TERMINAL_STATUSES:
        task.finished_at = now
    task.execution_log = [*task.execution_log, _log_entry(status, message, now)]
    session.commit()
    session.refresh(task)


def _asset_snapshot(asset: Asset) -> dict[str, object]:
    return {
        "id": asset.id, "name": asset.name, "host": asset.host,
        "ssh_port": asset.ssh_port, "os_family": asset.os_family.value,
        "environment": asset.environment.value, "group_name": asset.group_name,
        "credential_configured": asset.credential_id is not None,
    }


def _log_entry(status: ScanStatus, message: str, occurred_at: datetime) -> dict[str, object]:
    return {"status": status.value, "message": message, "occurred_at": occurred_at.isoformat()}


def _build_summary(
    task: ScanTask, results: list[dict[str, object]], findings: list[dict[str, object]]
) -> dict[str, object]:
    counts = {
        level: sum(item["severity"] == level for item in findings)
        for level in ("critical", "high", "medium", "low")
    }
    return {
        "assets_total": len(task.asset_snapshot),
        "assets_scanned": sum(item["status"] == "scanned" for item in results),
        "assets_failed": sum(item["status"] == "failed" for item in results),
        **counts,
        "manual_confirmation": sum(
            item["conclusion"] == "manual_confirmation" for item in findings
        ),
        "evidence_policy": "vendor_advisory_first", "collection_mode": "read_only",
        "asset_results": [
            {key: value for key, value in item.items() if key != "package_versions"}
            for item in results
        ],
    }


def _build_report(task: ScanTask, summary: dict[str, object]) -> str:
    targets = "、".join(str(item["name"]) for item in task.asset_snapshot)
    services = _service_report(summary)
    return (
        f"Linux 漏洞哨兵扫描报告\n任务：{task.name}\n扫描类型：{task.scan_type.value}\n"
        f"执行模式：只读、非破坏\n目标资产：{targets}\n知识库版本：{task.knowledge_version or '未发布'}\n"
        f"采集覆盖：{summary['assets_scanned']}/{summary['assets_total']}，失败 {summary['assets_failed']}\n"
        f"严重：{summary['critical']} 高危：{summary['high']} "
        f"中危：{summary['medium']} 低危：{summary['low']}\n"
        "服务证据：监听地址 -> 进程 -> 可执行文件所属软件包 -> 厂商公告；无法映射不判定。\n"
        f"{services}"
        "判定规则：厂商安全公告优先；无法确认 backport 时标记需人工确认。\n"
    )


def _service_report(summary: dict[str, object]) -> str:
    lines: list[str] = []
    for result in summary.get("asset_results", []):
        if not isinstance(result, dict):
            continue
        for service in result.get("services", []):
            if not isinstance(service, dict):
                continue
            package = service.get("package_name") or "未映射"
            version = service.get("installed_version") or "未知版本"
            lines.append(
                f"- {result.get('asset_name', '未知资产')} {service.get('listen', '未知监听')} "
                f"{service.get('process_name') or '未知进程'} {package} {version}\n"
            )
    return "".join(lines) if lines else "- 未采集到可映射的监听服务\n"
