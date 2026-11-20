"""Register Kubernetes backup artifacts and verification state."""
import sqlalchemy as sa
from alembic import op

revision = "20261120_57"
down_revision = "20261119_56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kubernetes_backup_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "cluster_id", sa.String(36),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster_revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("namespaces", sa.JSON(), nullable=False),
        sa.Column("include_persistent_volumes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="registered"),
        sa.Column("verification_error", sa.String(160)),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("restore_target", sa.JSON()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("cluster_id", "name", name="uq_kubernetes_backup_cluster_name"),
    )
    op.create_index(
        "ix_kubernetes_backup_artifacts_cluster_created",
        "kubernetes_backup_artifacts", ["cluster_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kubernetes_backup_artifacts_cluster_created",
        table_name="kubernetes_backup_artifacts",
    )
    op.drop_table("kubernetes_backup_artifacts")
