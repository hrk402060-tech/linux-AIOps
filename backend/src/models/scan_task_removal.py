from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ScanTaskRemoval(Base):
    __tablename__ = 'scan_task_removals'

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey('scan_tasks.id', ondelete='RESTRICT'), unique=True, nullable=False,
    )
    operator: Mapped[str] = mapped_column(String(120))
    operator_user_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    evidence_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    removed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True,
    )
