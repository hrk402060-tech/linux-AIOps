from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ExposureStatus(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExposureTask(Base):
    __tablename__ = "exposure_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[ExposureStatus] = mapped_column(
        SqlEnum(ExposureStatus, name="exposure_status"), index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    methodology_version: Mapped[str] = mapped_column(String(40))
    asset_ids: Mapped[list[str]] = mapped_column(JSON)
    asset_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
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
