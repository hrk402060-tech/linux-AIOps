from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AssetCleanupAudit(Base):
    __tablename__ = "asset_cleanup_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_name: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(120), index=True)
    evidence_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
