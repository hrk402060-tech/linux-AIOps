from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScanReport(Base):
    __tablename__ = "scan_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_task_id: Mapped[str] = mapped_column(
        ForeignKey("scan_tasks.id", ondelete="RESTRICT"), index=True
    )
    scan_task_name: Mapped[str] = mapped_column(String(120), index=True)
    format: Mapped[str] = mapped_column(String(12), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    asset_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    knowledge_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    vulnerability_summary: Mapped[dict[str, int]] = mapped_column(JSON)
    finding_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_by: Mapped[str] = mapped_column(String(120))
    audit_history: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
