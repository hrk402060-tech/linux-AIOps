import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.models.asset import Asset, AssetLifecycleStatus, AssetStatus
from src.models.monitoring import (
    MonitoringAlertState, MonitoringEvent, MonitoringPolicy, MonitoringSnapshot,
)
from src.schemas.monitoring import MonitoringPolicyUpdate
from src.services import monitor_collector
from src.services import monitoring_alert_bridge
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.scan_engine import ScanCollectionError

LOGGER = logging.getLogger(__name__)
METRICS = {
    "cpu_percent": ("cpu_threshold", "CPU 使用率"),
    "memory_percent": ("memory_threshold", "内存使用率"),
    "swap_percent": ("swap_threshold", "Swap 使用率"),
    "disk_percent": ("disk_threshold", "磁盘使用率"),
    "inode_percent": ("inode_threshold", "inode 使用率"),
    "load_1m": ("load_threshold", "1 分钟负载"),
}


class MonitoringError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def ensure_policy(session: Session, asset_id: str) -> MonitoringPolicy:
    policy = session.get(MonitoringPolicy, asset_id)
    if policy is None:
        policy = MonitoringPolicy(asset_id=asset_id)
        session.add(policy)
        session.flush()
    return policy


def collect_asset(
    session: Session, asset: Asset, vault: CredentialVault
) -> MonitoringSnapshot:
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise MonitoringError("已停用或归档资产不能执行监控采集")
    now = _now()
    try:
        if asset.credential is None:
            raise MonitoringError("资产未绑定 SSH 凭据")
        credential = asset.credential
        secret = vault.decrypt(credential.encrypted_secret, credential.nonce, credential.id, credential.credential_type.value, credential.key_version)
        metrics = monitor_collector.collect_metrics(asset, credential, secret)
        snapshot = MonitoringSnapshot(id=str(uuid4()), asset_id=asset.id, status="online", metrics=metrics, read_only=True, collected_at=now)
        asset.status = AssetStatus.ONLINE
        asset.check_message = "只读 SSH 监控采集成功"
        credential.last_used_at = now
        policy = ensure_policy(session, asset.id)
        _availability(session, asset, policy, True, now)
        _evaluate(session, asset, policy, metrics, now)
        monitoring_alert_bridge.sync_events(
            session,
            asset,
            [
                item for item in session.new
                if isinstance(item, MonitoringEvent)
            ],
            vault,
        )
    except (
        CredentialCryptoError, InvalidTag, MonitoringError, ScanCollectionError,
        UnicodeError,
    ) as exc:
        LOGGER.warning("Monitoring collection failed for asset %s: %s", asset.id, type(exc).__name__)
        detail = str(exc)
        safe_prefixes = (
            "SSH 登录认证失败", "SSH 端口无法连接", "SSH 连接超时",
            "SSH 网络连接失败", "SSH 连接失败", "SSH 私钥格式无效",
        )
        message = detail[:255] if detail.startswith(safe_prefixes) else (
            f"只读 SSH 采集失败：{type(exc).__name__}"
        )
        snapshot = MonitoringSnapshot(id=str(uuid4()), asset_id=asset.id, status="offline", metrics={}, error_message=message, read_only=True, collected_at=now)
        asset.status = AssetStatus.OFFLINE
        asset.check_message = snapshot.error_message
        _availability(session, asset, ensure_policy(session, asset.id), False, now)
        monitoring_alert_bridge.sync_events(
            session,
            asset,
            [
                item for item in session.new
                if isinstance(item, MonitoringEvent)
            ],
            vault,
        )
    asset.last_checked_at = now
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


def _in_maintenance(policy: MonitoringPolicy, now: datetime) -> bool:
    start, end = _aware(policy.maintenance_start), _aware(policy.maintenance_end)
    return bool(start and end and start <= now <= end)


def _availability(
    session: Session, asset: Asset, policy: MonitoringPolicy, online: bool, now: datetime
) -> None:
    state = session.scalar(select(MonitoringAlertState).where(
        MonitoringAlertState.asset_id == asset.id,
        MonitoringAlertState.metric == "availability",
    ))
    if state is None:
        state = MonitoringAlertState(
            id=str(uuid4()), asset_id=asset.id, metric="availability", status="normal",
            last_value=float(online), threshold=1,
        )
        session.add(state)
    state.last_value = float(online)
    if online:
        if state.status == "triggered" and policy.recovery_notify:
            _event(session, asset, "availability", "recovered", 1, 1, "服务器连接已恢复", now)
        state.status, state.exceeded_since, state.triggered_at = "normal", None, None
        return
    if _in_maintenance(policy, now):
        if state.status != "suppressed":
            _event(session, asset, "availability", "suppressed", 0, 1, "服务器离线，维护窗口内已抑制", now)
        state.status, state.exceeded_since, state.triggered_at = "suppressed", None, None
        return
    if state.status in {"normal", "suppressed"}:
        state.status, state.exceeded_since = "pending", now
    exceeded_since = _aware(state.exceeded_since) or now
    if state.status == "pending" and now - exceeded_since >= timedelta(minutes=policy.duration_minutes):
        state.status, state.triggered_at = "triggered", now
        _event(session, asset, "availability", "triggered", 0, 1, "服务器持续离线", now)


def _evaluate(
    session: Session, asset: Asset, policy: MonitoringPolicy, metrics: dict[str, object], now: datetime
) -> None:
    for metric, (field, label) in METRICS.items():
        value = float(metrics.get(metric, 0))
        threshold = float(getattr(policy, field))
        state = session.scalar(select(MonitoringAlertState).where(MonitoringAlertState.asset_id == asset.id, MonitoringAlertState.metric == metric))
        if state is None:
            state = MonitoringAlertState(id=str(uuid4()), asset_id=asset.id, metric=metric, status="normal", last_value=value, threshold=threshold)
            session.add(state)
        _transition(session, asset, policy, state, label, value, threshold, now)


def _transition(
    session: Session, asset: Asset, policy: MonitoringPolicy, state: MonitoringAlertState,
    label: str, value: float, threshold: float, now: datetime,
) -> None:
    state.last_value, state.threshold = value, threshold
    if value <= threshold:
        if state.status == "triggered" and policy.recovery_notify:
            _event(session, asset, state.metric, "recovered", value, threshold, f"{label}已恢复", now)
        state.status, state.exceeded_since, state.triggered_at = "normal", None, None
        return
    if _in_maintenance(policy, now):
        if state.status != "suppressed":
            _event(session, asset, state.metric, "suppressed", value, threshold, f"{label}超限，维护窗口内已抑制", now)
        state.status, state.exceeded_since, state.triggered_at = "suppressed", None, None
        return
    if state.status in {"normal", "suppressed"}:
        state.status, state.exceeded_since = "pending", now
    exceeded_since = _aware(state.exceeded_since) or now
    if state.status == "pending" and now - exceeded_since >= timedelta(minutes=policy.duration_minutes):
        state.status, state.triggered_at = "triggered", now
        _event(session, asset, state.metric, "triggered", value, threshold, f"{label}持续超限", now)


def _event(
    session: Session, asset: Asset, metric: str, kind: str, value: float,
    threshold: float, message: str, now: datetime,
) -> None:
    session.add(MonitoringEvent(id=str(uuid4()), asset_id=asset.id, asset_name=asset.name, metric=metric, event_type=kind, value=value, threshold=threshold, message=message, occurred_at=now))


def update_policy(
    session: Session, asset: Asset, payload: MonitoringPolicyUpdate
) -> MonitoringPolicy:
    policy = ensure_policy(session, asset.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    start, end = _aware(policy.maintenance_start), _aware(policy.maintenance_end)
    if start and end and end <= start:
        raise MonitoringError("维护结束时间必须晚于开始时间")
    session.commit()
    session.refresh(policy)
    return policy


def list_snapshots(
    session: Session, asset_id: str, limit: int, hours: int
) -> list[MonitoringSnapshot]:
    since = _now() - timedelta(hours=hours)
    return list(session.scalars(select(MonitoringSnapshot).where(MonitoringSnapshot.asset_id == asset_id, MonitoringSnapshot.collected_at >= since).order_by(MonitoringSnapshot.collected_at.desc()).limit(limit)))


def list_events(
    session: Session, limit: int, visible_asset_ids: set[str] | None = None
) -> list[MonitoringEvent]:
    statement = select(MonitoringEvent)
    if visible_asset_ids is not None:
        statement = statement.where(MonitoringEvent.asset_id.in_(visible_asset_ids))
    return list(session.scalars(
        statement.order_by(MonitoringEvent.occurred_at.desc()).limit(limit)
    ))


def get_overview(
    session: Session, visible_asset_ids: set[str] | None = None
) -> dict[str, object]:
    statement = select(Asset).where(
        Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED
    )
    if visible_asset_ids is not None:
        statement = statement.where(Asset.id.in_(visible_asset_ids))
    assets = list(session.scalars(statement.order_by(Asset.name)))
    rows: list[dict[str, object]] = []
    maintenance = abnormal = 0
    now = _now()
    for asset in assets:
        policy = ensure_policy(session, asset.id)
        latest = session.scalar(select(MonitoringSnapshot).where(MonitoringSnapshot.asset_id == asset.id).order_by(MonitoringSnapshot.collected_at.desc()).limit(1))
        alerts = session.scalar(select(func.count()).select_from(MonitoringAlertState).where(MonitoringAlertState.asset_id == asset.id, MonitoringAlertState.status == "triggered")) or 0
        abnormal += int(alerts > 0)
        maintenance += int(_in_maintenance(policy, now))
        rows.append({"asset_id": asset.id, "asset_name": asset.name, "host": asset.host, "latest_snapshot": latest, "active_alerts": alerts, "policy": policy})
    session.commit()
    online = sum(1 for row in rows if row["latest_snapshot"] is not None and row["latest_snapshot"].status == "online")
    offline = sum(1 for row in rows if row["latest_snapshot"] is not None and row["latest_snapshot"].status == "offline")
    rows.sort(key=lambda row: (int(row["active_alerts"]), row["asset_name"]), reverse=True)
    return {"summary": {"total": len(rows), "online": online, "offline": offline, "abnormal": abnormal, "maintenance": maintenance}, "assets": rows}


async def run_scheduler(
    session_factory: sessionmaker[Session], vault: CredentialVault, interval: int, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass
        with session_factory() as session:
            asset_ids = list(session.scalars(select(Asset.id).where(
                Asset.credential_id.is_not(None),
                Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
            )))
        for asset_id in asset_ids:
            if stop.is_set():
                return
            await asyncio.to_thread(
                _collect_asset_by_id, session_factory, vault, asset_id
            )


def _collect_asset_by_id(
    session_factory: sessionmaker[Session], vault: CredentialVault, asset_id: str
) -> None:
    with session_factory() as session:
        asset = session.get(Asset, asset_id)
        if asset is not None:
            collect_asset(session, asset, vault)
