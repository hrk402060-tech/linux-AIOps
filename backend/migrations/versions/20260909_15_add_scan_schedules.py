"""Add read-only scan schedules."""
from alembic import op
import sqlalchemy as sa

revision = "20260909_15"
down_revision = "20260909_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scan_type", sa.String(24), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("asset_snapshot", sa.JSON(), nullable=False),
        sa.Column("schedule_kind", sa.String(20), nullable=False),
        sa.Column("daily_time", sa.String(5)),
        sa.Column("interval_hours", sa.Integer()),
        sa.Column("timezone", sa.String(60), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_task_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_task_id"], ["scan_tasks.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_scan_schedules_name", "scan_schedules", ["name"])
    op.create_index("ix_scan_schedules_enabled", "scan_schedules", ["enabled"])
    op.create_index("ix_scan_schedules_next_run_at", "scan_schedules", ["next_run_at"])
    op.add_column("scan_tasks", sa.Column("schedule_id", sa.String(36)))
    op.create_index("ix_scan_tasks_schedule_id", "scan_tasks", ["schedule_id"])


def downgrade() -> None:
    op.drop_index("ix_scan_tasks_schedule_id", table_name="scan_tasks")
    op.drop_column("scan_tasks", "schedule_id")
    op.drop_table("scan_schedules")
