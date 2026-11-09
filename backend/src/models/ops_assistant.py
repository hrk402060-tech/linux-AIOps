from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OpsConversation(Base):
    __tablename__ = "ops_assistant_conversations"
    __table_args__ = (
        Index("ix_ops_conversations_owner_archived_updated", "owner_key", "archived", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(160), default="新会话")
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True,
    )
    redacted_context: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    active_request_id: Mapped[str | None] = mapped_column(String(36))
    request_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OpsMessage(Base):
    __tablename__ = "ops_assistant_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "number", name="uq_ops_message_number"),
        UniqueConstraint("conversation_id", "request_id", "role", name="uq_ops_message_request_role"),
        Index("ix_ops_messages_conversation_number", "conversation_id", "number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("ops_assistant_conversations.id", ondelete="CASCADE"),
    )
    number: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    error_message: Mapped[str | None] = mapped_column(String(255))
    analysis: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
