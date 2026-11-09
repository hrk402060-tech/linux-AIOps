"""Add external alerts and immutable root-cause evidence."""

from alembic import op
import sqlalchemy as sa

revision = "20260918_25"
down_revision = "20260918_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_connectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column("secret_hint", sa.String(8), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("name", name="uq_alert_connectors_name"),
    )
    for column in ("name", "source_type", "status", "operator"):
        op.create_index(
            f"ix_alert_connectors_{column}", "alert_connectors", [column]
        )

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connector_id", sa.String(36),
            sa.ForeignKey("alert_connectors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(180), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "asset_id", sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("sanitized_payload", sa.JSON(), nullable=False),
        sa.Column(
            "incident_id", sa.String(36),
            sa.ForeignKey("incidents.id", ondelete="RESTRICT"),
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "connector_id", "external_id", name="uq_alert_external"
        ),
    )
    for column in (
        "connector_id", "severity", "status", "asset_id", "incident_id",
        "received_at", "updated_at",
    ):
        op.create_index(f"ix_alerts_{column}", "alerts", [column])

    op.create_table(
        "alert_timeline",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "alert_id", sa.String(36),
            sa.ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "connector_id", sa.String(36),
            sa.ForeignKey("alert_connectors.id", ondelete="RESTRICT"),
        ),
        sa.Column("request_nonce_hash", sa.String(64)),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("previous_status", sa.String(24)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "connector_id", "request_nonce_hash", name="uq_alert_ingest_nonce"
        ),
    )
    for column in (
        "alert_id", "connector_id", "request_nonce_hash", "event_type",
        "status", "operator", "trace_id", "created_at",
    ):
        op.create_index(
            f"ix_alert_timeline_{column}", "alert_timeline", [column]
        )

    op.create_table(
        "root_cause_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "alert_id", sa.String(36),
            sa.ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "asset_id", sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for column in (
        "alert_id", "asset_id", "status", "trigger_type", "operator",
        "trace_id",
    ):
        op.create_index(
            f"ix_root_cause_analyses_{column}", "root_cause_analyses", [column]
        )


def downgrade() -> None:
    op.drop_table("root_cause_analyses")
    op.drop_table("alert_timeline")
    op.drop_table("alerts")
    op.drop_table("alert_connectors")
