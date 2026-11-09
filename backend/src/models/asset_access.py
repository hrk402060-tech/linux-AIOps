from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserAssetAccess(Base):
    __tablename__ = "user_asset_access"
    __table_args__ = (
        UniqueConstraint("user_id", "asset_id", name="uq_user_asset_access"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_ssh: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class UserAccessAudit(Base):
    __tablename__ = "user_access_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24), index=True)
    before_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    operator_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operator_username: Mapped[str] = mapped_column(String(100), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
