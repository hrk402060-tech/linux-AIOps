"""create agent recommendations

Revision ID: 20260904_06
Revises: 20260903_05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260904_06"
down_revision: str | None = "20260903_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("finding_id", sa.String(length=36), nullable=False),
        sa.Column("cve_id", sa.String(length=40), nullable=False),
        sa.Column("asset_name", sa.String(length=120), nullable=False),
        sa.Column("provider_id", sa.String(length=80), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("risk_analysis", sa.Text(), nullable=False),
        sa.Column("remediation_steps", sa.JSON(), nullable=False),
        sa.Column("validation_steps", sa.JSON(), nullable=False),
        sa.Column("rollback_plan", sa.Text(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["vulnerability_findings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("finding_id", "cve_id", "asset_name", "status", "priority", "created_at"):
        op.create_index(
            f"ix_agent_recommendations_{column}", "agent_recommendations", [column]
        )


def downgrade() -> None:
    op.drop_table("agent_recommendations")
