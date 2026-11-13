from datetime import datetime, timezone
import hashlib
import logging
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.alert_notification import AlertNotification, AlertNotificationAudit, AlertNotificationReceipt
from src.schemas.alert_notification import NotificationResponse
from src.services.alert_notification_config import get_record

logger = logging.getLogger(__name__)


class NotificationNotFoundError(LookupError):
    pass


class NotificationConflictError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    session: Session, dedupe_key: str, kind: str, alert_id: str | None,
    analysis_id: str | None, subject: str, body: str,
) -> AlertNotification:
    from src.services.alert_security import redact_text

    if kind not in {'alert_received', 'diagnostic_result', 'asset_mapping_failed', 'recovery_result'}:
        raise NotificationConflictError('Unknown notification kind')
    existing = session.scalar(select(AlertNotification).where(AlertNotification.dedupe_key == dedupe_key))
    existing = existing or next((item for item in session.new if isinstance(item, AlertNotification) and item.dedupe_key == dedupe_key), None)
    if existing is not None:
        return existing
    channel = get_record(session)
    enabled = bool(channel and channel.enabled and channel.recipients)
    item = AlertNotification(
        id=str(uuid4()), dedupe_key=dedupe_key, kind=kind, alert_id=alert_id, analysis_id=analysis_id,
        subject=redact_text(subject).replace('\r', ' ').replace('\n', ' ')[:200],
        body=redact_text(body)[:8000], status='pending' if enabled else 'blocked',
        asset_locator={},
        recipients=list(channel.recipients) if enabled and channel else [],
        attempts=0, max_attempts=5, created_at=utc_now(),
        error_code=None if enabled else 'notification_not_configured' if channel is None else 'notification_disabled',
        next_attempt_at=utc_now() if enabled else None,
    )
    session.add(item)
    return item


def enqueue_mapping_failure(
    session: Session, connector_id: str, external_id: str, nonce_hash: str,
    title: str, failure: str,
    source: str | None = None, locators: dict[str, str] | None = None,
) -> AlertNotification:
    from src.services.alert_security import redact_text

    if mapping_nonce_used(session, connector_id, nonce_hash):
        raise NotificationConflictError('Alert request nonce was already used')
    key = hashlib.sha256(f'{connector_id}:{external_id}'.encode()).hexdigest()
    item = enqueue(session, f'mapping:{key}', 'asset_mapping_failed', None, None,
                   f'告警资产关联失败: {title}', f'有效签名告警无法关联唯一启用资产。\n原因: {failure}\n未执行 SSH；需要管理员关联资产。')
    if item in session.new:
        item.source = redact_text(source or connector_id)[:120]
        item.external_id = redact_text(external_id)[:180]
        item.asset_locator = {key: redact_text(str(value))[:300] for key, value in (locators or {}).items()
                              if key in {'asset_id', 'asset_host', 'instance'}}
        correlation = f'\n来源: {item.source}\n外部 ID: {item.external_id}\n资产定位: {item.asset_locator}'
        item.body = (item.body + correlation)[:8000]
    session.flush()
    session.add(AlertNotificationReceipt(id=str(uuid4()), connector_id=connector_id,
                                        external_id=redact_text(external_id)[:180], nonce_hash=nonce_hash, notification_id=item.id))
    return item


def mapping_nonce_used(session: Session, connector_id: str, nonce_hash: str) -> bool:
    return session.scalar(select(AlertNotificationReceipt.id).where(
        AlertNotificationReceipt.connector_id == connector_id,
        AlertNotificationReceipt.nonce_hash == nonce_hash,
    )) is not None


def get_notification(session: Session, notification_id: str) -> AlertNotification:
    item = session.get(AlertNotification, notification_id)
    if item is None:
        raise NotificationNotFoundError('Notification not found')
    return item


def serialize(item: AlertNotification) -> dict[str, object]:
    return NotificationResponse.model_validate(item).model_dump()


def list_notifications(
    session: Session, offset: int, limit: int,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    statement = select(AlertNotification)
    if visible_asset_ids is not None:
        statement = statement.join(
            Alert, Alert.id == AlertNotification.alert_id,
            isouter=True,
        ).where(
            AlertNotification.alert_id.is_(None)
            | Alert.asset_id.in_(visible_asset_ids)
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = session.scalars(statement.order_by(
        AlertNotification.created_at.desc(), AlertNotification.id.desc()
    ).offset(offset).limit(min(limit, 100))).all()
    return {'total': total, 'items': [serialize(item) for item in items]}


def list_for_alert(session: Session, alert_id: str) -> dict[str, object]:
    items = session.scalars(select(AlertNotification).where(AlertNotification.alert_id == alert_id)
                           .order_by(AlertNotification.created_at.desc(), AlertNotification.id.desc()).limit(100)).all()
    return {'items': [serialize(item) for item in items]}


def summarize(
    session: Session, alert_id: str | None, analysis_id: str | None = None,
) -> dict[str, object]:
    rows = session.execute(select(AlertNotification.status, func.count(), func.count(AlertNotification.acknowledged_at))
                           .where(AlertNotification.alert_id == alert_id).group_by(AlertNotification.status)).all()
    result = {'total': 0, 'sent': 0, 'failed': 0, 'blocked': 0, 'pending': 0, 'acknowledged': 0}
    for status, count, acknowledged in rows:
        result['total'] += count
        result['acknowledged'] += acknowledged
        result['pending' if status in {'pending', 'retry', 'sending'} else status] += count
    result['diagnostic_results'] = session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.alert_id == alert_id, AlertNotification.kind == 'diagnostic_result')) or 0
    latest = None
    if analysis_id is not None:
        item = session.scalar(select(AlertNotification).where(
            AlertNotification.alert_id == alert_id,
            AlertNotification.analysis_id == analysis_id,
            AlertNotification.kind == 'diagnostic_result',
        ).order_by(AlertNotification.created_at.desc(), AlertNotification.id.desc()).limit(1))
        if item is not None:
            latest = {
                'id': item.id,
                'analysis_id': item.analysis_id,
                'status': item.status,
                'error_code': item.error_code,
                'acknowledged': item.acknowledged_at is not None,
            }
    return {**result, 'latest_diagnostic': latest}


def retry_notification(session: Session, notification_id: str, operator: str) -> AlertNotification:
    item = get_notification(session, notification_id)
    if item.status in {'sent', 'sending'} or item.acknowledged_at is not None:
        raise NotificationConflictError('已发送、已确认或正在发送的通知不能重试')
    if item.attempts >= item.max_attempts:
        raise NotificationConflictError('通知已达到重试上限；需管理员排查渠道故障，不能重置次数')
    channel = get_record(session)
    enabled = bool(channel and channel.enabled and channel.recipients)
    changes = {'status': 'pending' if enabled else 'blocked', 'next_attempt_at': utc_now() if enabled else None,
               'error_code': None if enabled else 'notification_not_configured' if channel is None else 'notification_disabled',
               'claim_token': None, 'lease_expires_at': None}
    changed = session.execute(update(AlertNotification).where(
        AlertNotification.id == item.id, AlertNotification.status.in_({'pending', 'blocked', 'retry', 'failed'}),
        AlertNotification.acknowledged_at.is_(None), AlertNotification.attempts < AlertNotification.max_attempts,
    ).values(**changes)).rowcount
    if not changed:
        session.rollback()
        raise NotificationConflictError('Notification changed; reload before retrying')
    _audit(session, item.id, 'retry_requested', operator, '人工请求通知重试；保留累计次数')
    _commit(session)
    session.refresh(item)
    return item


def acknowledge(session: Session, notification_id: str, operator: str) -> AlertNotification:
    item = get_notification(session, notification_id)
    if item.status != 'sent':
        raise NotificationConflictError('只有 SMTP 已接受的通知可以登记人工确认')
    if item.acknowledged_at is not None:
        return item
    changed = session.execute(update(AlertNotification).where(
        AlertNotification.id == item.id, AlertNotification.status == 'sent', AlertNotification.acknowledged_at.is_(None),
    ).values(acknowledged_at=utc_now(), acknowledged_by=operator)).rowcount
    if changed:
        _audit(session, item.id, 'acknowledged', operator, '管理员已人工确认通知；与 SMTP 接受状态分开')
    _commit(session)
    session.refresh(item)
    return item


def _audit(session: Session, notification_id: str, action: str, operator: str, detail: str) -> None:
    session.add(AlertNotificationAudit(id=str(uuid4()), notification_id=notification_id,
                                     action=action, operator=operator, detail=detail))


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.warning('Notification transaction failed')
        raise
