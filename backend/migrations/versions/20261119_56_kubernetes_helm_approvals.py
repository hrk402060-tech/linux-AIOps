"""Persist Helm preview evidence and independent review decisions."""
import sqlalchemy as sa
from alembic import op

revision = "20261119_56"
down_revision = "20261117_55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kubernetes_helm_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_revision", sa.Integer(), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("chart", sa.JSON(), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("release_name", sa.String(53), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requester_id", sa.String(36), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("reviewer_id", sa.String(36)),
        sa.Column("reviewed_by", sa.String(120)),
        sa.Column("reason", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_kubernetes_helm_approvals_cluster_created", "kubernetes_helm_approvals", ["cluster_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_kubernetes_helm_approvals_cluster_created", table_name="kubernetes_helm_approvals")
    op.drop_table("kubernetes_helm_approvals")
