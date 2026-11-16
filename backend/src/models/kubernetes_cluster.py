from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class KubernetesCluster(Base):
    __tablename__ = "kubernetes_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(512))
    context: Mapped[str] = mapped_column(String(128))
    service_id: Mapped[str | None] = mapped_column(
        ForeignKey("cmdb_services.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    connection_status: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown"
    )
    authorization_status: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown"
    )
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capabilities: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    server_cert_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ca_cert_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_cert_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    credential_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    credential: Mapped["KubernetesClusterCredential | None"] = relationship(
        back_populates="cluster", uselist=False, cascade="all, delete-orphan"
    )
    audits: Mapped[list["KubernetesClusterAudit"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": False}


class KubernetesClusterCredential(Base):
    __tablename__ = "kubernetes_cluster_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), unique=True, index=True
    )
    auth_type: Mapped[str] = mapped_column(String(24))
    encrypted_config: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    key_version: Mapped[str] = mapped_column(String(32))
    credential_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    identity_hint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    cluster: Mapped[KubernetesCluster] = relationship(back_populates="credential")


class KubernetesClusterAudit(Base):
    __tablename__ = "kubernetes_cluster_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(24))
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    cluster: Mapped[KubernetesCluster] = relationship(back_populates="audits")


class KubernetesConnectionTest(Base):
    __tablename__ = "kubernetes_connection_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))
    config_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
