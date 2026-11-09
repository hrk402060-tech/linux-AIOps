from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.models.alert import utc_now


class DiagnosticAdmission(Base):
    __tablename__ = 'diagnostic_admission'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class DiagnosticJob(Base):
    __tablename__ = 'diagnostic_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey('alerts.id', ondelete='RESTRICT'), index=True)
    active_alert_id: Mapped[str | None] = mapped_column(
        ForeignKey('alerts.id', ondelete='RESTRICT'), unique=True, nullable=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id', ondelete='RESTRICT'), index=True)
    grant_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    trigger_type: Mapped[str] = mapped_column(String(16))
    operator: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    current_analysis_id: Mapped[str | None] = mapped_column(String(36))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiagnosticSlot(Base):
    __tablename__ = 'diagnostic_slots'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('diagnostic_jobs.id', ondelete='RESTRICT'))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
