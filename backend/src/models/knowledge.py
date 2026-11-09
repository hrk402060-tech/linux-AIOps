from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(50))
    sync_mode: Mapped[str] = mapped_column(String(30), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(default=True)
    online_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    schedule: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeRelease(Base):
    __tablename__ = "knowledge_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    package_id: Mapped[str] = mapped_column(String(120))
    source_name: Mapped[str] = mapped_column(String(100))
    signature_key_id: Mapped[str] = mapped_column(String(100))
    content_sha256: Mapped[str] = mapped_column(String(64))
    advisory_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advisories: Mapped[list["KnowledgeAdvisory"]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )


class KnowledgeAdvisory(Base):
    __tablename__ = "knowledge_advisories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_releases.id", ondelete="CASCADE"), index=True
    )
    cve_id: Mapped[str] = mapped_column(String(32), index=True)
    advisory_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    os_family: Mapped[str] = mapped_column(String(30), index=True)
    source_name: Mapped[str] = mapped_column(String(100))
    vendor_url: Mapped[str] = mapped_column(String(500))
    affected_packages: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    release: Mapped[KnowledgeRelease] = relationship(back_populates="advisories")


class KnowledgeSyncRun(Base):
    __tablename__ = "knowledge_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_key_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    advisory_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeBackfillJob(Base):
    __tablename__ = "knowledge_backfill_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)
    current_page: Mapped[int] = mapped_column(Integer, default=1)
    total_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batch_size: Mapped[int] = mapped_column(Integer)
    delay_seconds: Mapped[int] = mapped_column(Integer)
    processed_notices: Mapped[int] = mapped_column(Integer, default=0)
    published_advisories: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_release_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_releases.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
