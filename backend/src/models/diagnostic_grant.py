from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosticGrant(Base):
    __tablename__ = 'diagnostic_grants'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id', ondelete='RESTRICT'), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default='linux', server_default='linux')
    kubernetes_config: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    docker_config: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    mysql_config: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    redis_config: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    rabbitmq_config: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(64))
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    key_version: Mapped[str] = mapped_column(String(32))
    key_fingerprint: Mapped[str] = mapped_column(String(128))
    host_fingerprint: Mapped[str] = mapped_column(String(128))
    host_public_key: Mapped[str] = mapped_column(Text)
    host_key_type: Mapped[str] = mapped_column(String(32))
    action_ids: Mapped[list[str]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    operator: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiagnosticGrantAudit(Base):
    __tablename__ = 'diagnostic_grant_audits'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id', ondelete='RESTRICT'), index=True)
    action: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
