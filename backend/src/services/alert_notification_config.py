import logging
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert_notification import AlertNotification, AlertNotificationAudit, AlertNotificationConfig, utc_now
from src.schemas.alert_notification import NotificationConfigUpdate
from src.services.crypto import CredentialVault

logger = logging.getLogger(__name__)
CONFIG_ID = 'smtp-admin'
SECRET_TYPE = 'alert_notification_smtp'


class NotificationConfigError(ValueError):
    pass


def get_record(session: Session) -> AlertNotificationConfig | None:
    return session.get(AlertNotificationConfig, CONFIG_ID, populate_existing=True)


def serialize(item: AlertNotificationConfig | None) -> dict[str, object]:
    return {
        'configured': item is not None, 'enabled': item.enabled if item else False,
        'host': item.host if item else '', 'port': item.port if item else 587,
        'security': item.security if item else 'starttls', 'username': item.username if item else '',
        'sender': item.sender if item else '', 'recipients': item.recipients if item else [],
        'password_configured': bool(item and item.encrypted_password),
        'updated_at': item.updated_at if item else None,
    }


def save_config(
    session: Session, payload: NotificationConfigUpdate, operator: str, vault: CredentialVault,
) -> AlertNotificationConfig:
    try:
        item = session.scalar(select(AlertNotificationConfig).where(
            AlertNotificationConfig.id == CONFIG_ID
        ).with_for_update())
        if item is None:
            item = AlertNotificationConfig(id=CONFIG_ID, enabled=False, host='', port=587,
                                           security='starttls', username='', sender='', recipients=[])
            session.add(item)
        for field in ('host', 'port', 'security', 'username', 'sender', 'recipients'):
            value = getattr(payload, field)
            if value is not None:
                setattr(item, field, value)
        item.enabled = payload.enabled
        if item.enabled and not (item.host and item.sender and item.recipients):
            raise NotificationConfigError('启用通知必须配置 SMTP 主机、发件人和接收人')
        if payload.password is not None:
            item.encrypted_password, item.password_nonce = vault.encrypt(payload.password, item.id, SECRET_TYPE)
            item.key_version = vault.key_version
        if item.enabled and item.username and not item.encrypted_password:
            raise NotificationConfigError('填写 SMTP 用户名时必须配置密码')
        item.updated_at = utc_now()
        _update_waiting(session, item)
        session.add(AlertNotificationAudit(id=str(uuid4()), action='configuration_updated',
                                         operator=operator, detail='SMTP 通知配置已更新；未记录认证信息'))
        session.commit()
    except (SQLAlchemyError, NotificationConfigError):
        session.rollback()
        logger.warning('SMTP notification configuration update failed')
        raise
    session.refresh(item)
    return item


def _update_waiting(session: Session, item: AlertNotificationConfig) -> None:
    if item.enabled:
        session.execute(update(AlertNotification).where(
            AlertNotification.status == 'blocked',
            AlertNotification.attempts < AlertNotification.max_attempts,
        ).values(status='pending', error_code=None, next_attempt_at=utc_now()))
    else:
        session.execute(update(AlertNotification).where(
            AlertNotification.status.in_(('pending', 'retry')),
        ).values(status='blocked', error_code='notification_disabled', next_attempt_at=None))
