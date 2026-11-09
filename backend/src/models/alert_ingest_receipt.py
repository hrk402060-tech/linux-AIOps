from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AlertIngestReceipt(Base):
    __tablename__ = "alert_ingest_receipts"
    __table_args__ = (
        CheckConstraint("result IN ('success', 'failure')", name="ck_ingest_result"),
        CheckConstraint(
            "outcome IN ('created', 'duplicate', 'reopened', 'rejected')",
            name="ck_ingest_outcome",
        ),
        Index("ix_ingest_connector_time", "connector_id", "received_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid4()))
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("alert_connectors.id", ondelete="RESTRICT"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    result: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    http_status: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    alert_id: Mapped[str | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=True,
    )
