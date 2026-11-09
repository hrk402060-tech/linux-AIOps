from datetime import datetime, timezone

from sqlalchemy import (
    DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertConnector(Base):
    __tablename__ = "alert_connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    key_version: Mapped[str] = mapped_column(String(32))
    secret_hint: Mapped[str] = mapped_column(String(8))
    operator: Mapped[str] = mapped_column(String(120), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("connector_id", "external_id", name="uq_alert_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("alert_connectors.id", ondelete="RESTRICT"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(180))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    asset_name: Mapped[str] = mapped_column(String(100))
    sanitized_payload: Mapped[dict[str, object]] = mapped_column(JSON)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_key: Mapped[str | None] = mapped_column(String(220), nullable=True, index=True)
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, index=True
    )


class AlertTimeline(Base):
    __tablename__ = "alert_timeline"
    __table_args__ = (
        UniqueConstraint(
            "connector_id", "request_nonce_hash", name="uq_alert_ingest_nonce"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), index=True
    )
    connector_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_connectors.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    request_nonce_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    note: Mapped[str] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class RootCauseAnalysis(Base):
    __tablename__ = "root_cause_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    trigger_type: Mapped[str] = mapped_column(String(16), index=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey('diagnostic_jobs.id', ondelete='RESTRICT'), nullable=True, index=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    operator: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
