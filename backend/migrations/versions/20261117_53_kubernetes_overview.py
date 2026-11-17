"""Persist bounded Kubernetes overview projections and recoverable collection leases."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261117_53"
down_revision: str | Sequence[str] | None = "20261116_52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kubernetes_collection_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cluster_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False, unique=True),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column("requester_revision", sa.Integer(), nullable=True),
        sa.Column("requester_session_id", sa.String(36), nullable=True),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("cluster_revision", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projection", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["kubernetes_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requester_session_id"], ["auth_sessions.id"], ondelete="SET NULL"),
    )
    for column in ("cluster_id", "state"):
        op.create_index(f"ix_kubernetes_collection_runs_{column}", "kubernetes_collection_runs", [column])
    op.create_index(
        "uq_kubernetes_collection_active", "kubernetes_collection_runs", ["cluster_id"], unique=True,
        sqlite_where=sa.text("state IN ('queued', 'running')"),
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_kubernetes_collection_active", table_name="kubernetes_collection_runs")
    for column in ("state", "cluster_id"):
        op.drop_index(f"ix_kubernetes_collection_runs_{column}", table_name="kubernetes_collection_runs")
    op.drop_table("kubernetes_collection_runs")
