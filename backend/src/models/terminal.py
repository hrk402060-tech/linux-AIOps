from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TerminalSession(Base):
    __tablename__ = "terminal_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    asset_name: Mapped[str] = mapped_column(String(100), index=True)
    host: Mapped[str] = mapped_column(String(253))
    operator: Mapped[str] = mapped_column(String(100), index=True)
    client_ip: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), index=True)
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    ticket_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TerminalAssistantAnalysis(Base):
    __tablename__ = "terminal_assistant_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    terminal_session_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_sessions.id", ondelete="RESTRICT"), index=True
    )
    mode: Mapped[str] = mapped_column(String(24), index=True)
    redacted_context: Mapped[str] = mapped_column(Text)
    diagnosis: Mapped[str] = mapped_column(Text)
    plan: Mapped[list[str]] = mapped_column(JSON)
    validation_steps: Mapped[list[str]] = mapped_column(JSON)
    operator: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TerminalCommandAudit(Base):
    __tablename__ = "terminal_command_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_assistant_analyses.id", ondelete="RESTRICT"), index=True
    )
    terminal_session_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_sessions.id", ondelete="RESTRICT"), index=True
    )
    command: Mapped[str] = mapped_column(String(1000))
    purpose: Mapped[str] = mapped_column(String(500))
    validation_step: Mapped[str] = mapped_column(String(500))
    risk_level: Mapped[str] = mapped_column(String(12), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    confirmation_phrase: Mapped[str] = mapped_column(String(80))
    requested_by: Mapped[str] = mapped_column(String(100))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
