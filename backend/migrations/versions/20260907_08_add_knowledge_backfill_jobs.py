"""add knowledge backfill jobs

Revision ID: 20260907_08
Revises: 20260904_07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260907_08"
down_revision: str | None = "20260904_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_backfill_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_page", sa.Integer(), nullable=False),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("delay_seconds", sa.Integer(), nullable=False),
        sa.Column("processed_notices", sa.Integer(), nullable=False),
        sa.Column("published_advisories", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_release_id", sa.String(36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_release_id"], ["knowledge_releases.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_knowledge_backfill_jobs_source_id", "knowledge_backfill_jobs", ["source_id"])
    op.create_index("ix_knowledge_backfill_jobs_status", "knowledge_backfill_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_backfill_jobs_status", table_name="knowledge_backfill_jobs")
    op.drop_index("ix_knowledge_backfill_jobs_source_id", table_name="knowledge_backfill_jobs")
    op.drop_table("knowledge_backfill_jobs")
