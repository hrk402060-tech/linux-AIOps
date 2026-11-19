from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class KubernetesHelmApproval(Base):
    __tablename__ = "kubernetes_helm_approvals"
    __table_args__ = (Index("ix_kubernetes_helm_approvals_cluster_created", "cluster_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"))
    cluster_revision: Mapped[int]
    credential_revision: Mapped[int]
    policy_digest: Mapped[str] = mapped_column(String(64))
    source_digest: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    chart: Mapped[dict[str, str]] = mapped_column(JSON)
    resources: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    namespace: Mapped[str] = mapped_column(String(63))
    release_name: Mapped[str] = mapped_column(String(53))
    status: Mapped[str] = mapped_column(String(16))
    requester_id: Mapped[str] = mapped_column(String(36))
    requested_by: Mapped[str] = mapped_column(String(120))
    reviewer_id: Mapped[str | None] = mapped_column(String(36))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
