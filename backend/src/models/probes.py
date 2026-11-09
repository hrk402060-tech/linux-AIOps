from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HttpProbe(Base):
    __tablename__ = "http_probes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    service_id: Mapped[str] = mapped_column(ForeignKey("cmdb_services.id", ondelete="RESTRICT"), index=True)
    api_id: Mapped[str | None] = mapped_column(ForeignKey("cmdb_service_apis.id", ondelete="RESTRICT"), nullable=True, index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    method: Mapped[str] = mapped_column(String(8))
    expected_status: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    operator_username: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HttpProbeRun(Base):
    __tablename__ = "http_probe_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    probe_id: Mapped[str] = mapped_column(ForeignKey("http_probes.id", ondelete="RESTRICT"), index=True)
    business_id: Mapped[str] = mapped_column(String(36), index=True)
    application_id: Mapped[str] = mapped_column(String(36), index=True)
    service_id: Mapped[str] = mapped_column(String(36), index=True)
    api_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    asset_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    operator_username: Mapped[str] = mapped_column(String(120), index=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
