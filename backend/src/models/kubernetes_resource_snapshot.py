from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.models.kubernetes_cluster import utc_now


class KubernetesResourceSnapshot(Base):
    __tablename__ = "kubernetes_resource_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "uid", name="uq_kubernetes_resource_snapshot_identity"),
        Index("ix_kubernetes_resource_snapshots_cluster_kind", "cluster_id", "kind"),
        Index("ix_kubernetes_resource_snapshots_cluster_namespace", "cluster_id", "namespace"),
        Index("ix_kubernetes_resource_snapshots_cluster_name", "cluster_id", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_collection_runs.id", ondelete="CASCADE"), index=True,
    )
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), index=True,
    )
    kind: Mapped[str] = mapped_column(String(32))
    namespace: Mapped[str | None] = mapped_column(String(253), nullable=True)
    uid: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(253))
    resource_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    projection: Mapped[dict[str, object]] = mapped_column(JSON)
