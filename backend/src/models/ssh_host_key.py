from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SshHostKey(Base):
    __tablename__ = "ssh_host_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), unique=True, index=True
    )
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column()
    key_type: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    public_key: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(120))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
