"""Add manually triggered service HTTP probes and immutable runs."""

from alembic import op
import sqlalchemy as sa

revision = "20260917_23"
down_revision = "20260915_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "http_probes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("service_id", sa.String(36), sa.ForeignKey("cmdb_services.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("api_id", sa.String(36), sa.ForeignKey("cmdb_service_apis.id", ondelete="RESTRICT")),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("expected_status", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("operator_username", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("service_id", "api_id", "asset_id", "status"):
        op.create_index(f"ix_http_probes_{column}", "http_probes", [column])
    op.create_table(
        "http_probe_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("probe_id", sa.String(36), sa.ForeignKey("http_probes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("business_id", sa.String(36), nullable=False),
        sa.Column("application_id", sa.String(36), nullable=False),
        sa.Column("service_id", sa.String(36), nullable=False),
        sa.Column("api_id", sa.String(36)),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("operator_username", sa.String(120), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("probe_id", "business_id", "application_id", "service_id", "api_id", "asset_id", "status", "operator_username", "trace_id", "checked_at"):
        op.create_index(f"ix_http_probe_runs_{column}", "http_probe_runs", [column])


def downgrade() -> None:
    op.drop_table("http_probe_runs")
    op.drop_table("http_probes")
