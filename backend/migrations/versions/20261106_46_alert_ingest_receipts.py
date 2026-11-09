"""Record safe alert connector ingestion outcomes."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261106_46"
down_revision: str | Sequence[str] | None = "20261031_45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_ingest_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connector_id", sa.String(36),
                  sa.ForeignKey("alert_connectors.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("alert_id", sa.String(36),
                  sa.ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=True),
        sa.CheckConstraint("result IN ('success', 'failure')",
                           name="ck_ingest_result"),
        sa.CheckConstraint(
            "outcome IN ('created', 'duplicate', 'reopened', 'rejected')",
            name="ck_ingest_outcome",
        ),
    )
    op.create_index("ix_ingest_connector_time", "alert_ingest_receipts",
                    ["connector_id", "received_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_ingest_connector_time", table_name="alert_ingest_receipts")
    op.drop_table("alert_ingest_receipts")
