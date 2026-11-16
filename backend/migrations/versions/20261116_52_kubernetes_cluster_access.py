"""Add isolated Kubernetes cluster connection management tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_52"
down_revision: str | Sequence[str] | None = "20261110_51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kubernetes_clusters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("context", sa.String(length=128), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=True),
        sa.Column("lifecycle", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("connection_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("authorization_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("server_cert_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ca_cert_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_cert_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_interval_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("credential_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["cmdb_services.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("name", name="uq_kubernetes_clusters_name"),
    )
    op.create_index("ix_kubernetes_clusters_name", "kubernetes_clusters", ["name"], unique=False)
    op.create_index("ix_kubernetes_clusters_service_id", "kubernetes_clusters", ["service_id"], unique=False)
    op.create_table(
        "kubernetes_cluster_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cluster_id", sa.String(length=36), nullable=False),
        sa.Column("auth_type", sa.String(length=24), nullable=False),
        sa.Column("encrypted_config", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("identity_hint", sa.String(length=256), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["kubernetes_clusters.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("cluster_id", name="uq_kubernetes_cluster_credentials_cluster_id"),
    )
    op.create_index("ix_kubernetes_cluster_credentials_cluster_id", "kubernetes_cluster_credentials", ["cluster_id"], unique=False)
    op.create_table(
        "kubernetes_cluster_audits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cluster_id", sa.String(length=36), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["kubernetes_clusters.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_kubernetes_cluster_audits_cluster_id", "kubernetes_cluster_audits", ["cluster_id"], unique=False)
    op.create_table(
        "kubernetes_connection_tests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kubernetes_connection_tests_expires_at", "kubernetes_connection_tests", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_kubernetes_connection_tests_expires_at", table_name="kubernetes_connection_tests")
    op.drop_table("kubernetes_connection_tests")
    op.drop_index("ix_kubernetes_cluster_audits_cluster_id", table_name="kubernetes_cluster_audits")
    op.drop_table("kubernetes_cluster_audits")
    op.drop_index("ix_kubernetes_cluster_credentials_cluster_id", table_name="kubernetes_cluster_credentials")
    op.drop_table("kubernetes_cluster_credentials")
    op.drop_index("ix_kubernetes_clusters_service_id", table_name="kubernetes_clusters")
    op.drop_index("ix_kubernetes_clusters_name", table_name="kubernetes_clusters")
    op.drop_table("kubernetes_clusters")
