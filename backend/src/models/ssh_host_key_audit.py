from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class SshHostKeyAudit(Base):
    __tablename__ = "ssh_host_key_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    key_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(24), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128))
    operator: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
