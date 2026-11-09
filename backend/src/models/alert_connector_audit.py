from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertConnectorAudit(Base):
    __tablename__ = "alert_connector_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("alert_connectors.id", ondelete="RESTRICT"), index=True)
    connector_name: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(24), index=True)
    result: Mapped[str] = mapped_column(String(16), index=True)
    detail: Mapped[str] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(120), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
