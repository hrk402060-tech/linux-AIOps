"""Add immutable alert connector operation audits."""

from alembic import op
import sqlalchemy as sa

revision = "20260925_27"
down_revision = "20260922_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_connector_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36), nullable=False),
        sa.Column("connector_name", sa.String(120), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connector_id"], ["alert_connectors.id"], ondelete="RESTRICT"),
    )
    for column in ("connector_id", "connector_name", "action", "result", "operator", "occurred_at"):
        op.create_index(f"ix_alert_connector_audits_{column}", "alert_connector_audits", [column])


def downgrade() -> None:
    op.drop_table("alert_connector_audits")
