"""Persist bounded Kubernetes resource snapshots for read-only browsing."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261117_54"
down_revision: str | Sequence[str] | None = "20261117_53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kubernetes_resource_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("cluster_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("namespace", sa.String(253), nullable=True),
        sa.Column("uid", sa.String(256), nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("resource_version", sa.String(128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["kubernetes_collection_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["kubernetes_clusters.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "kind", "uid", name="uq_kubernetes_resource_snapshot_identity"),
    )
    op.create_index(
        "ix_kubernetes_resource_snapshots_run_id",
        "kubernetes_resource_snapshots",
        ["run_id"],
    )
    op.create_index(
        "ix_kubernetes_resource_snapshots_cluster_kind",
        "kubernetes_resource_snapshots",
        ["cluster_id", "kind"],
    )
    op.create_index(
        "ix_kubernetes_resource_snapshots_cluster_namespace",
        "kubernetes_resource_snapshots",
        ["cluster_id", "namespace"],
    )
    op.create_index(
        "ix_kubernetes_resource_snapshots_cluster_name",
        "kubernetes_resource_snapshots",
        ["cluster_id", "name"],
    )


def downgrade() -> None:
    op.drop_index("ix_kubernetes_resource_snapshots_cluster_name", table_name="kubernetes_resource_snapshots")
    op.drop_index("ix_kubernetes_resource_snapshots_cluster_namespace", table_name="kubernetes_resource_snapshots")
    op.drop_index("ix_kubernetes_resource_snapshots_cluster_kind", table_name="kubernetes_resource_snapshots")
    op.drop_index("ix_kubernetes_resource_snapshots_run_id", table_name="kubernetes_resource_snapshots")
    op.drop_table("kubernetes_resource_snapshots")
