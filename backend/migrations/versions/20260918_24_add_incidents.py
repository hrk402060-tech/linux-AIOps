"""Add incident projection and immutable incident timeline."""

from alembic import op
import sqlalchemy as sa

revision = "20260918_24"
down_revision = "20260917_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "asset_id", sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("alert_id", sa.String(180)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    for column in (
        "severity", "status", "asset_id", "alert_id", "task_id", "operator",
        "trace_id", "created_at", "updated_at",
    ):
        op.create_index(f"ix_incidents_{column}", "incidents", [column])
    op.create_table(
        "incident_timeline",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "incident_id", sa.String(36),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("previous_status", sa.String(24)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "incident_id", "status", "operator", "trace_id", "created_at",
    ):
        op.create_index(
            f"ix_incident_timeline_{column}", "incident_timeline", [column]
        )


def downgrade() -> None:
    op.drop_table("incident_timeline")
    op.drop_table("incidents")
