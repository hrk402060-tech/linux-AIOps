from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class CredentialRiskStatus(str, Enum):
    QUEUED = "queued"
    ASSESSING = "assessing"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CredentialRiskTask(Base):
    __tablename__ = "credential_risk_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[CredentialRiskStatus] = mapped_column(
        SqlEnum(CredentialRiskStatus, name="credential_risk_status"), index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    offline_only: Mapped[bool] = mapped_column(Boolean, default=True)
    policy_version: Mapped[str] = mapped_column(String(40))
    credential_ids: Mapped[list[str]] = mapped_column(JSON)
    credential_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    results: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    execution_log: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rerun_of_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
