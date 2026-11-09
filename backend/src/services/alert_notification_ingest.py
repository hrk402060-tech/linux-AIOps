"""Transactional notification hooks; caller commits with alert evidence."""
import json
import logging
from uuid import uuid4

from sqlalchemy import select

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, AlertTimeline, RootCauseAnalysis
from src.models.alert_notification import AlertNotificationReceipt
from src.schemas.alert import AlertIngest
from src.services import alert_notifications
from src.services import alert_ingest_runtime
from src.services.alert_security import AlertConflictError, redact

logger = logging.getLogger(__name__)


def record_receipt(
    session: Session, alert: Alert, connector_id: str, nonce_hash: str,
    recurrence: bool = False,
) -> None:
    # Do not autoflush: a uniqueness race must be handled by the caller's commit.
    with session.no_autoflush:
        alert_ingest_runtime.record_success(session, alert, recurrence)
        dedupe_key = (
            f"received:{alert.id}:recurrence:{nonce_hash}"
            if recurrence else f"received:{alert.id}"
        )
        event_label = "告警复发" if recurrence else "告警接收"
        item = alert_notifications.enqueue(
            session, dedupe_key, "alert_received", alert.id, None,
            f"{event_label}: {alert.title}",
            f"告警: {alert.id}\n外部编号: {alert.external_id}\n资产: {alert.asset_name}"
            f"\n级别: {alert.severity}\n描述: {alert.description}"
            f"\n{event_label}，定位结果将另行通知，恢复尚未验证。"
            "\n排查入口: 平台 > 监控中心 > 活动告警 > 外部告警研判，按告警编号查询。",
        )
        session.add(AlertNotificationReceipt(
            id=str(uuid4()), connector_id=connector_id, external_id=alert.external_id,
            nonce_hash=nonce_hash, notification_id=item.id,
        ))


def enqueue_diagnosis(session: Session, alert: Alert, analysis: RootCauseAnalysis) -> None:
    evidence = json.dumps(redact(analysis.evidence), ensure_ascii=False, default=str)[:4000]
    with session.no_autoflush:
        alert_notifications.enqueue(
            session, f"diagnostic:{analysis.id}", "diagnostic_result", alert.id, analysis.id,
            f"告警定位结果: {alert.title}",
            f"告警: {alert.id}\n外部编号: {alert.external_id}\n资产: {alert.asset_name}"
            f"\n定位状态: {analysis.status}\n结论: {analysis.summary}"
            f"\n失败原因: {analysis.error_code or '无'}\n证据: {evidence}"
            f"\n策略: {analysis.policy_version}\ntrace_id: {analysis.trace_id}"
            "\n根因与恢复未经验证，需管理员人工排查。AI 无写入或修复权限。"
            "\n排查入口: 平台 > 监控中心 > 活动告警 > 外部告警研判，按告警编号查询。",
        )


def persist_mapping_failure(
    session: Session, connector_id: str, payload: AlertIngest, nonce_hash: str, failure: str,
) -> None:
    for attempt in range(2):
        try:
            connector = session.get(AlertConnector, connector_id)
            locators = {
                key: str(value) for key in ("asset_id", "asset_host", "instance")
                if (value := getattr(payload, key)) is not None
            }
            alert_notifications.enqueue_mapping_failure(
                session, connector_id, payload.external_id, nonce_hash, payload.title, failure,
                source=connector.name if connector else connector_id, locators=locators,
            )
            session.commit()
            return
        except IntegrityError as exc:
            session.rollback()
            if nonce_used(session, connector_id, nonce_hash):
                raise AlertConflictError("Alert request nonce was already used") from exc
            if attempt:
                raise AlertConflictError("Mapping receipt conflicts with existing evidence") from exc
        except alert_notifications.NotificationConflictError as exc:
            session.rollback()
            raise AlertConflictError(str(exc)) from exc
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Mapping notification transaction failed")
            raise


def nonce_used(session: Session, connector_id: str, nonce_hash: str) -> bool:
    return alert_notifications.mapping_nonce_used(session, connector_id, nonce_hash) or session.scalar(
        select(AlertTimeline.id).where(
            AlertTimeline.connector_id == connector_id,
            AlertTimeline.request_nonce_hash == nonce_hash,
        )
    ) is not None
