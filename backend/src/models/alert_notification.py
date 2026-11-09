from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertNotificationConfig(Base):
    __tablename__ = 'alert_notification_config'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=587)
    security: Mapped[str] = mapped_column(String(16), default='starttls')
    username: Mapped[str] = mapped_column(String(200), default='')
    sender: Mapped[str] = mapped_column(String(254))
    recipients: Mapped[list[str]] = mapped_column(JSON, default=list)
    encrypted_password: Mapped[bytes | None] = mapped_column(LargeBinary)
    password_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
    key_version: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlertNotification(Base):
    __tablename__ = 'alert_notifications'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey('alerts.id', ondelete='RESTRICT'), index=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey('root_cause_analyses.id', ondelete='RESTRICT'), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    recovery_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey('recovery_evaluations.id', ondelete='RESTRICT'), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(180))
    asset_locator: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    recipients: Mapped[list[str]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(120))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AlertNotificationReceipt(Base):
    __tablename__ = 'alert_notification_receipts'
    __table_args__ = (UniqueConstraint('connector_id', 'nonce_hash', name='uq_notification_receipt_nonce'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey('alert_connectors.id', ondelete='RESTRICT'), index=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(180))
    notification_id: Mapped[str] = mapped_column(ForeignKey('alert_notifications.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AlertNotificationAudit(Base):
    __tablename__ = 'alert_notification_audits'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    notification_id: Mapped[str | None] = mapped_column(ForeignKey('alert_notifications.id', ondelete='RESTRICT'), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    operator: Mapped[str] = mapped_column(String(120), index=True)
    detail: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
