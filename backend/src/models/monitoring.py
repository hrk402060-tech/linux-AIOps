from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MonitoringPolicy(Base):
    __tablename__ = "monitoring_policies"

    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    cpu_threshold: Mapped[float] = mapped_column(Float, default=85)
    memory_threshold: Mapped[float] = mapped_column(Float, default=85)
    swap_threshold: Mapped[float] = mapped_column(Float, default=80)
    disk_threshold: Mapped[float] = mapped_column(Float, default=85)
    inode_threshold: Mapped[float] = mapped_column(Float, default=85)
    load_threshold: Mapped[float] = mapped_column(Float, default=8)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=5)
    recovery_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    maintenance_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    severity_rules: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MonitoringSnapshot(Base):
    __tablename__ = "monitoring_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class MonitoringAlertState(Base):
    __tablename__ = "monitoring_alert_states"
    __table_args__ = (UniqueConstraint("asset_id", "metric", name="uq_monitor_alert_metric"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    metric: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    exceeded_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)


class MonitoringEvent(Base):
    __tablename__ = "monitoring_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    asset_name: Mapped[str] = mapped_column(String(100), index=True)
    metric: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)
    value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
