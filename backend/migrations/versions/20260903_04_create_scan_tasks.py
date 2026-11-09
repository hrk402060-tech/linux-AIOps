"""Create auditable scan tasks.

Revision ID: 20260903_04
Revises: 20260903_03
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260903_04"
down_revision: str | None = "20260903_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scan_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("asset_snapshot", sa.JSON(), nullable=False),
        sa.Column("knowledge_release_id", sa.String(36), nullable=True),
        sa.Column("knowledge_version", sa.String(80), nullable=True),
        sa.Column("rerun_of_id", sa.String(36), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("execution_log", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("report_content", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_release_id"], ["knowledge_releases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rerun_of_id"], ["scan_tasks.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_scan_tasks_name", "scan_tasks", ["name"])
    op.create_index("ix_scan_tasks_scan_type", "scan_tasks", ["scan_type"])
    op.create_index("ix_scan_tasks_status", "scan_tasks", ["status"])
    op.create_index("ix_scan_tasks_knowledge_release_id", "scan_tasks", ["knowledge_release_id"])
    op.create_index("ix_scan_tasks_rerun_of_id", "scan_tasks", ["rerun_of_id"])


def downgrade() -> None:
    for column in ("rerun_of_id", "knowledge_release_id", "status", "scan_type", "name"):
        op.drop_index(f"ix_scan_tasks_{column}", table_name="scan_tasks")
    op.drop_table("scan_tasks")
