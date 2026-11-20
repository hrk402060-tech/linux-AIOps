from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.models.kubernetes_cluster import utc_now


class KubernetesBackupArtifact(Base):
    __tablename__ = "kubernetes_backup_artifacts"
    __table_args__ = (
        Index("ix_kubernetes_backup_artifacts_cluster_created", "cluster_id", "created_at"),
        UniqueConstraint("cluster_id", "name", name="uq_kubernetes_backup_cluster_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"),
    )
    cluster_revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(63))
    storage_key: Mapped[str] = mapped_column(String(255))
    namespaces: Mapped[list[str]] = mapped_column(JSON, default=list)
    include_persistent_volumes: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="registered", server_default="registered")
    verification_error: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restore_target: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
