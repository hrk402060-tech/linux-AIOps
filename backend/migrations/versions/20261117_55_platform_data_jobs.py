"""Persist safe storage cleanup and verified encrypted backup jobs."""
from alembic import op
import sqlalchemy as sa

revision = "20261117_55"
down_revision = "20261117_54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_data_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_key", sa.String(16), nullable=True),
        sa.Column("operator_username", sa.String(120), nullable=False),
        sa.Column("operator_user_id", sa.String(36), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("artifact_directory", sa.String(255), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("active_key", name="uq_platform_data_active"),
    )
    op.create_index("ix_platform_data_jobs_status", "platform_data_jobs", ["status"])
    op.create_index("ix_platform_data_jobs_created_at", "platform_data_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_platform_data_jobs_created_at", table_name="platform_data_jobs")
    op.drop_index("ix_platform_data_jobs_status", table_name="platform_data_jobs")
    op.drop_table("platform_data_jobs")
