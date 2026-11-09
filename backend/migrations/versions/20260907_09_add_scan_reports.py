"""add scan reports

Revision ID: 20260907_09
Revises: 20260907_08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260907_09"
down_revision: str | None = "20260907_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scan_task_id", sa.String(36), nullable=False),
        sa.Column("scan_task_name", sa.String(120), nullable=False),
        sa.Column("format", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("asset_snapshot", sa.JSON(), nullable=False),
        sa.Column("knowledge_version", sa.String(80), nullable=True),
        sa.Column("vulnerability_summary", sa.JSON(), nullable=False),
        sa.Column("finding_snapshot", sa.JSON(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("file_content", sa.LargeBinary(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("generated_by", sa.String(120), nullable=False),
        sa.Column("audit_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scan_task_id"], ["scan_tasks.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_scan_reports_scan_task_id", "scan_reports", ["scan_task_id"])
    op.create_index("ix_scan_reports_scan_task_name", "scan_reports", ["scan_task_name"])
    op.create_index("ix_scan_reports_format", "scan_reports", ["format"])
    op.create_index("ix_scan_reports_status", "scan_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scan_reports_status", table_name="scan_reports")
    op.drop_index("ix_scan_reports_format", table_name="scan_reports")
    op.drop_index("ix_scan_reports_scan_task_name", table_name="scan_reports")
    op.drop_index("ix_scan_reports_scan_task_id", table_name="scan_reports")
    op.drop_table("scan_reports")
