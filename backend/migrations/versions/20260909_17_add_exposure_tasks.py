"""Add immutable exposure surface tasks."""
from alembic import op
import sqlalchemy as sa

revision = "20260909_17"
down_revision = "20260909_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exposure_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "SCANNING", "AGGREGATING", "COMPLETED", "FAILED", "CANCELLED", name="exposure_status"), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False), sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("methodology_version", sa.String(40), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False), sa.Column("asset_snapshot", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON()), sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("execution_log", sa.JSON(), nullable=False), sa.Column("error_message", sa.Text()),
        sa.Column("rerun_of_id", sa.String(36)), sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exposure_tasks_name", "exposure_tasks", ["name"])
    op.create_index("ix_exposure_tasks_status", "exposure_tasks", ["status"])
    op.create_index("ix_exposure_tasks_rerun_of_id", "exposure_tasks", ["rerun_of_id"])


def downgrade() -> None:
    op.drop_table("exposure_tasks")
