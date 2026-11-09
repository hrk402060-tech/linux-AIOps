from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryVerification(Base):
    __tablename__ = 'recovery_verifications'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey('alerts.id', ondelete='RESTRICT'), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id', ondelete='RESTRICT'), index=True)
    active_alert_id: Mapped[str | None] = mapped_column(
        ForeignKey('alerts.id', ondelete='RESTRICT'), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    metric: Mapped[str | None] = mapped_column(String(32))
    threshold: Mapped[float | None] = mapped_column(Float)
    probe_id: Mapped[str | None] = mapped_column(ForeignKey('http_probes.id', ondelete='RESTRICT'))
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    observation_minutes: Mapped[int] = mapped_column(Integer)
    max_gap_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    operator: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryEvaluation(Base):
    __tablename__ = 'recovery_evaluations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    verification_id: Mapped[str] = mapped_column(
        ForeignKey('recovery_verifications.id', ondelete='RESTRICT'), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(120))
    trace_id: Mapped[str] = mapped_column(String(36))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON)
