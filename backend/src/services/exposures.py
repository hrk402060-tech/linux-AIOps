from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.exposure import ExposureStatus, ExposureTask
from src.schemas.exposure import ExposureTaskCreate
from src.services import exposure_collector
from src.services.asset_access import (
    AssetAuthorizationError, all_assets_visible, evidence_asset_ids,
    require_background_assets,
)
from src.services.crypto import CredentialVault

TERMINAL = {ExposureStatus.COMPLETED, ExposureStatus.FAILED, ExposureStatus.CANCELLED}
TRANSITIONS = {
    ExposureStatus.QUEUED: {ExposureStatus.SCANNING, ExposureStatus.CANCELLED},
    ExposureStatus.SCANNING: {ExposureStatus.AGGREGATING, ExposureStatus.FAILED, ExposureStatus.CANCELLED},
    ExposureStatus.AGGREGATING: {ExposureStatus.COMPLETED, ExposureStatus.FAILED, ExposureStatus.CANCELLED},
}


class ExposureError(ValueError):
    pass


def list_tasks(
    session: Session, visible_asset_ids: set[str] | None = None
) -> list[ExposureTask]:
    tasks = list(session.scalars(select(ExposureTask).order_by(ExposureTask.created_at.desc())))
    if visible_asset_ids is None:
        return tasks
    return [
        task for task in tasks
        if all_assets_visible(task_asset_ids(task), visible_asset_ids)
    ]


def task_asset_ids(task: ExposureTask) -> set[str]:
    return evidence_asset_ids(
        task.asset_ids or [], task.asset_snapshot or [], task.results or [],
    )


def create_task(
    session: Session, payload: ExposureTaskCreate, rerun_of_id: str | None = None
) -> ExposureTask:
    assets = list(session.scalars(select(Asset).where(Asset.id.in_(payload.asset_ids))))
    if len(assets) != len(set(payload.asset_ids)):
        raise ExposureError("部分检测资产不存在")
    if any(asset.lifecycle_status != AssetLifecycleStatus.ENABLED for asset in assets):
        raise ExposureError("已停用或归档资产不能执行暴露面检测")
    ordered = {asset.id: asset for asset in assets}
    now = datetime.now(timezone.utc)
    task = ExposureTask(
        id=str(uuid4()), name=payload.name.strip(), status=ExposureStatus.QUEUED,
        progress=0, read_only=True, methodology_version=exposure_collector.METHODOLOGY_VERSION,
        asset_ids=payload.asset_ids, asset_snapshot=[_snapshot(ordered[item]) for item in payload.asset_ids],
        results=[], rerun_of_id=rerun_of_id,
        execution_log=[_log(ExposureStatus.QUEUED, "任务已进入只读检测队列", now)],
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def start_task(session: Session, task: ExposureTask) -> ExposureTask:
    if task.status != ExposureStatus.QUEUED:
        raise ExposureError("仅排队中的任务可以启动")
    enabled = set(session.scalars(select(Asset.id).where(
        Asset.id.in_(task.asset_ids), Asset.lifecycle_status == AssetLifecycleStatus.ENABLED
    )))
    if enabled != set(task.asset_ids):
        raise ExposureError("任务包含已停用或归档资产")
    return _transition(session, task, ExposureStatus.SCANNING, 10, "正在采集本机监听面")


def cancel_task(session: Session, task: ExposureTask) -> ExposureTask:
    if task.status in TERMINAL:
        raise ExposureError("终态任务不能取消")
    return _transition(session, task, ExposureStatus.CANCELLED, task.progress, "任务已由操作员取消")


def rerun_task(session: Session, task: ExposureTask) -> ExposureTask:
    if task.status not in TERMINAL:
        raise ExposureError("仅终态任务可以重跑")
    return create_task(
        session, ExposureTaskCreate(name=f"{task.name} - 重跑", asset_ids=task.asset_ids), task.id
    )


def execute_task(
    factory: sessionmaker[Session], task_id: str, vault: CredentialVault,
    operator_user_id: str | None = None, operator: str | None = None,
) -> None:
    operator_name = operator or "scheduler"
    with factory() as session:
        task = session.get(ExposureTask, task_id)
        if task is None or task.status != ExposureStatus.SCANNING:
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
                    listeners = exposure_collector.collect(asset, credential, secret)
                    require_background_assets(
                        session, operator_name, operator_user_id, task.asset_ids
                    )
                    results.append({"asset_id": asset.id, "asset_name": asset.name, "status": "checked", "listeners": listeners})
                except AssetAuthorizationError:
                    raise
                except Exception as exc:
                    results.append(_failure(asset.id, asset.name, type(exc).__name__, str(exc)[:500], asset))
            require_background_assets(session, operator_name, operator_user_id, task.asset_ids)
            task.results = results
            _transition(session, task, ExposureStatus.AGGREGATING, 85, "正在汇总监听范围")
            listeners = [item for result in results for item in result["listeners"]]
            task.summary = {
                "assets_total": len(task.asset_ids),
                "assets_checked": sum(result["status"] == "checked" for result in results),
                "listeners": len(listeners),
                "potential_exposures": sum(item["scope"] == "all_interfaces" for item in listeners),
                "loopback_only": sum(item["scope"] == "loopback" for item in listeners),
            }
            completed = any(result["status"] == "checked" for result in results)
            task.error_message = None if completed else next(
                (str(result.get("message", "")) for result in results if result.get("message")),
                "没有资产完成有效检测",
            )
            final = ExposureStatus.COMPLETED if completed else ExposureStatus.FAILED
            _transition(session, task, final, 100, "暴露面检测完成" if completed else "暴露面检测失败")
        except AssetAuthorizationError as exc:
            if task.status in {ExposureStatus.SCANNING, ExposureStatus.AGGREGATING}:
                task.error_message = str(exc)
                _transition(session, task, ExposureStatus.FAILED, task.progress, "暴露面检测因授权失效而停止")
        except Exception as exc:
            task.error_message = str(exc)[:1000]
            _transition(session, task, ExposureStatus.FAILED, task.progress, "暴露面检测执行失败")


def _failure(
    asset_id: str, asset_name: str, error_code: str, message: str,
    asset: Asset | None = None,
) -> dict[str, object]:
    return {
        "asset_id": asset_id, "asset_name": asset_name, "status": "failed",
        "listeners": [], "error_code": error_code, "message": message,
        "asset_snapshot": _snapshot(asset) if asset else None,
        "evidence": {
            "collection_mode": "fixed_read_only_ssh",
            "host_key_policy": "auto_accept_memory_only_v1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _transition(
    session: Session, task: ExposureTask, status: ExposureStatus, progress: int, message: str
) -> ExposureTask:
    if status not in TRANSITIONS.get(task.status, set()):
        raise ExposureError(f"非法状态迁移：{task.status.value} -> {status.value}")
    now = datetime.now(timezone.utc)
    task.status, task.progress = status, progress
    if status == ExposureStatus.SCANNING:
        task.started_at = now
    if status in TERMINAL:
        task.finished_at = now
    task.execution_log = [*task.execution_log, _log(status, message, now)]
    session.commit()
    session.refresh(task)
    return task


def _snapshot(asset: Asset) -> dict[str, object]:
    return {"id": asset.id, "name": asset.name, "host": asset.host, "os_family": asset.os_family.value}


def _log(status: ExposureStatus, message: str, occurred_at: datetime) -> dict[str, object]:
    return {"status": status.value, "message": message, "occurred_at": occurred_at.isoformat()}
