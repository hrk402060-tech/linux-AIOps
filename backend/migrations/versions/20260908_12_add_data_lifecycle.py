"""add asset and scan data lifecycle

Revision ID: 20260908_12
Revises: 20260908_11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260908_12"
down_revision: str | None = "20260908_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

asset_lifecycle = sa.Enum(
    "enabled", "disabled", "archived", name="asset_lifecycle_status"
)


def upgrade() -> None:
    asset_lifecycle.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "assets",
        sa.Column("lifecycle_status", asset_lifecycle, nullable=False, server_default="enabled"),
    )
    op.add_column("assets", sa.Column("lifecycle_reason", sa.String(255)))
    op.add_column("assets", sa.Column("lifecycle_operator", sa.String(120)))
    op.add_column("assets", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_index("ix_assets_lifecycle_status", "assets", ["lifecycle_status"])
    op.add_column(
        "scan_tasks",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("scan_tasks", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("scan_tasks", sa.Column("archived_by", sa.String(120)))
    op.create_index("ix_scan_tasks_archived", "scan_tasks", ["archived"])
    op.create_table(
        "asset_cleanup_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("asset_id", "operator", "deleted_at"):
        op.create_index(f"ix_asset_cleanup_audits_{column}", "asset_cleanup_audits", [column])


def downgrade() -> None:
    op.drop_table("asset_cleanup_audits")
    op.drop_index("ix_scan_tasks_archived", table_name="scan_tasks")
    for column in ("archived_by", "archived_at", "archived"):
        op.drop_column("scan_tasks", column)
    op.drop_index("ix_assets_lifecycle_status", table_name="assets")
    for column in ("archived_at", "lifecycle_operator", "lifecycle_reason", "lifecycle_status"):
        op.drop_column("assets", column)
    asset_lifecycle.drop(op.get_bind(), checkfirst=True)
