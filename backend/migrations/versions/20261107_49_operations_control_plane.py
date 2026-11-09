"""Add automation approvals and change records."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261107_49"
down_revision: str | Sequence[str] | None = "20261107_48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_automation_approvals_status_requested",
        "automation_approvals",
        ["status", "requested_at"],
    )
    op.create_table(
        "change_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=True),
        sa.Column("rollback_plan", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_change_records_status_created",
        "change_records",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_change_records_status_created", table_name="change_records")
    op.drop_table("change_records")
    op.drop_index(
        "ix_automation_approvals_status_requested",
        table_name="automation_approvals",
    )
    op.drop_table("automation_approvals")
