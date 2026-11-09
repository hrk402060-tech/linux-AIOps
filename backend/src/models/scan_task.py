from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def _enum_values(items: type[Enum]) -> list[str]:
    return [str(item.value) for item in items]


class ScanType(str, Enum):
    HOST_VULNERABILITY = "host_vulnerability"
    SERVICE_VULNERABILITY = "service_vulnerability"
    COMPREHENSIVE = "comprehensive"


class ScanStatus(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScanTask(Base):
    __tablename__ = "scan_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    scan_type: Mapped[ScanType] = mapped_column(
        SqlEnum(ScanType, name="scan_type", values_callable=_enum_values), index=True
    )
    status: Mapped[ScanStatus] = mapped_column(
        SqlEnum(ScanStatus, name="scan_status", values_callable=_enum_values), index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    asset_ids: Mapped[list[str]] = mapped_column(JSON)
    asset_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    knowledge_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_releases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    knowledge_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rerun_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    result_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    findings: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    execution_log: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ScanSchedule(Base):
    __tablename__ = "scan_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    scan_type: Mapped[ScanType] = mapped_column(
        SqlEnum(ScanType, name="schedule_scan_type", values_callable=_enum_values)
    )
    asset_ids: Mapped[list[str]] = mapped_column(JSON)
    asset_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    schedule_kind: Mapped[str] = mapped_column(String(20))
    daily_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
