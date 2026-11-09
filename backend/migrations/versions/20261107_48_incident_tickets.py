"""Add incident work-order tracking."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261107_48"
down_revision: str | Sequence[str] | None = "20261107_47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_tickets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "assignee_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assignee_username", sa.String(120), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "priority IN ('critical', 'high', 'medium', 'low')",
            name="ck_incident_ticket_priority",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name="ck_incident_ticket_status",
        ),
    )
    op.create_index(
        "ix_incident_tickets_incident_status",
        "incident_tickets",
        ["incident_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_incident_tickets_incident_status",
        table_name="incident_tickets",
    )
    op.drop_table("incident_tickets")
