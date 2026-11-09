from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AIProviderCredential(Base):
    __tablename__ = 'ai_provider_credentials'

    provider_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), default='', server_default='', nullable=False)
    model: Mapped[str] = mapped_column(String(200), default='', server_default='', nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30, server_default='30', nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class AIProviderAudit(Base):
    __tablename__ = 'ai_provider_audits'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey('ai_provider_credentials.provider_id'), index=True
    )
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(32), index=True)
    operator: Mapped[str] = mapped_column(String(120), index=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
