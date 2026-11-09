"""Promote threshold events into the shared alert workflow."""

import logging
import secrets
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, AlertTimeline
from src.models.asset import Asset
from src.models.monitoring import MonitoringEvent
from src.services import alert_notifications, diagnostic_jobs
from src.services.crypto import CredentialVault

LOGGER = logging.getLogger(__name__)
CONNECTOR_ID = "internal-monitoring"
CONNECTOR_NAME = "平台监控"
SECRET_TYPE = "alert_connector_hmac"


def sync_events(
    session: Session,
    asset: Asset,
    events: Iterable[MonitoringEvent],
    vault: CredentialVault,
) -> None:
    """Create one alert per active metric and record recovery evidence."""
    relevant = [
        event for event in events
        if event.asset_id == asset.id and event.event_type in {"triggered", "recovered"}
    ]
    if not relevant:
        return
    connector = _ensure_connector(session, vault)
    for event in relevant:
        if event.event_type == "triggered":
            _open_alert(session, connector, asset, event)
        else:
            _recover_alert(session, asset, event)


def _ensure_connector(session: Session, vault: CredentialVault) -> AlertConnector:
    connector = session.get(AlertConnector, CONNECTOR_ID)
    if connector is not None:
        return connector
    secret = secrets.token_urlsafe(32)
    encrypted, nonce = vault.encrypt(secret, CONNECTOR_ID, SECRET_TYPE)
    connector = AlertConnector(
        id=CONNECTOR_ID,
        name=CONNECTOR_NAME,
        source_type="internal_monitoring",
        encrypted_secret=encrypted,
        nonce=nonce,
        key_version=vault.key_version,
        secret_hint=secret[-4:],
        operator="system",
    )
    session.add(connector)
    session.flush()
    return connector


def _open_alert(
    session: Session, connector: AlertConnector, asset: Asset, event: MonitoringEvent,
) -> None:
    source_key = _source_key(asset.id, event.metric)
    current = _current_alert(session, source_key)
    if current is not None:
        _timeline(
            session, current, "monitoring_repeated",
            f"监控指标持续超限：{event.message}", "system",
        )
        return
    now = _aware(event.occurred_at)
    alert = Alert(
        id=str(uuid4()),
        connector_id=connector.id,
        external_id=f"monitor:{asset.id}:{event.metric}:{event.id}",
        title=f"{asset.name}：{event.message}",
        description=event.message,
        severity=_severity(event.metric),
        status="open",
        asset_id=asset.id,
        asset_name=asset.name,
        sanitized_payload={
            "source": "platform_monitoring",
            "metric": event.metric,
            "value": event.value,
            "threshold": event.threshold,
            "read_only": True,
        },
        source_type="platform_monitoring",
        source_key=source_key,
        received_at=now,
        updated_at=now,
    )
    session.add(alert)
    _timeline(session, alert, "monitoring_triggered", event.message, "system")
    session.flush()
    alert_notifications.enqueue(
        session,
        f"monitoring:{event.id}",
        "alert_received",
        alert.id,
        None,
        f"监控告警：{alert.title}",
        f"资产：{asset.name}\n指标：{event.metric}\n当前值：{event.value}\n阈值：{event.threshold}\n"
        "平台已登记告警并安排只读诊断，未执行任何修复。",
    )
    try:
        diagnostic_jobs.enqueue(session, alert, "automatic", "system", "监控阈值触发")
    except (SQLAlchemyError, RuntimeError, ValueError) as exc:
        LOGGER.warning("Monitoring diagnostic queue failed for %s: %s", alert.id, type(exc).__name__)
        _timeline(session, alert, "diagnostic_queue_failed", "只读诊断排队失败，需人工处理", "system")


def _recover_alert(session: Session, asset: Asset, event: MonitoringEvent) -> None:
    alert = _current_alert(session, _source_key(asset.id, event.metric))
    if alert is None:
        return
    previous = alert.status
    if previous in {"open", "acknowledged"}:
        alert.status = "resolved"
    alert.updated_at = _aware(event.occurred_at)
    _timeline(
        session,
        alert,
        "monitoring_recovered",
        "监控指标已恢复；仍需人工确认处置结果后关闭告警",
        "system",
        previous=previous,
    )
    alert_notifications.enqueue(
        session,
        f"monitoring-recovery:{event.id}",
        "recovery_result",
        alert.id,
        None,
        f"监控恢复：{alert.title}",
        f"资产：{asset.name}\n指标：{event.metric}\n恢复值：{event.value}\n"
        "平台已记录恢复证据，告警关闭仍需人工确认。",
    )


def _current_alert(session: Session, source_key: str) -> Alert | None:
    return session.scalar(
        select(Alert)
        .where(
            Alert.source_type == "platform_monitoring",
            Alert.source_key == source_key,
            Alert.status.notin_(("resolved", "closed")),
        )
        .order_by(Alert.received_at.desc(), Alert.id.desc())
        .limit(1)
    )


def _timeline(
    session: Session,
    alert: Alert,
    event_type: str,
    note: str,
    operator: str,
    previous: str | None = None,
) -> None:
    session.add(
        AlertTimeline(
            alert_id=alert.id,
            connector_id=alert.connector_id,
            event_type=event_type,
            previous_status=previous,
            status=alert.status,
            note=note,
            operator=operator,
            trace_id=str(uuid4()),
            details={"read_only": True, "source_type": "platform_monitoring"},
        )
    )


def _source_key(asset_id: str, metric: str) -> str:
    return f"{asset_id}:{metric}"


def _severity(metric: str) -> str:
    return "critical" if metric == "availability" else "high"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
