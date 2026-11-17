from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.models.kubernetes_cluster import utc_now


class KubernetesCollectionRun(Base):
    __tablename__ = "kubernetes_collection_runs"
    __table_args__ = (
        Index("uq_kubernetes_collection_active", "cluster_id", unique=True,
              sqlite_where=text("state IN ('queued', 'running')"),
              postgresql_where=text("state IN ('queued', 'running')")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), index=True,
    )
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    source: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str] = mapped_column(String(128), unique=True)
    requested_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    requester_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requester_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True,
    )
    credential_revision: Mapped[int] = mapped_column(Integer)
    cluster_revision: Mapped[int] = mapped_column(Integer)
    policy_digest: Mapped[str] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
