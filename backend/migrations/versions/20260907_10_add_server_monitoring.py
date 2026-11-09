"""add server monitoring

Revision ID: 20260907_10
Revises: 20260907_09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260907_10"
down_revision: str | None = "20260907_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_policies",
        sa.Column("asset_id", sa.String(36), primary_key=True),
        sa.Column("cpu_threshold", sa.Float(), nullable=False),
        sa.Column("memory_threshold", sa.Float(), nullable=False),
        sa.Column("swap_threshold", sa.Float(), nullable=False),
        sa.Column("disk_threshold", sa.Float(), nullable=False),
        sa.Column("inode_threshold", sa.Float(), nullable=False),
        sa.Column("load_threshold", sa.Float(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("recovery_notify", sa.Boolean(), nullable=False),
        sa.Column("maintenance_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "monitoring_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_monitoring_snapshots_asset_id", "monitoring_snapshots", ["asset_id"])
    op.create_index("ix_monitoring_snapshots_status", "monitoring_snapshots", ["status"])
    op.create_index("ix_monitoring_snapshots_collected_at", "monitoring_snapshots", ["collected_at"])
    op.create_table(
        "monitoring_alert_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("exceeded_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("asset_id", "metric", name="uq_monitor_alert_metric"),
    )
    op.create_index("ix_monitoring_alert_states_asset_id", "monitoring_alert_states", ["asset_id"])
    op.create_index("ix_monitoring_alert_states_status", "monitoring_alert_states", ["status"])
    op.create_table(
        "monitoring_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("message", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_monitoring_events_asset_id", "monitoring_events", ["asset_id"])
    op.create_index("ix_monitoring_events_asset_name", "monitoring_events", ["asset_name"])
    op.create_index("ix_monitoring_events_metric", "monitoring_events", ["metric"])
    op.create_index("ix_monitoring_events_event_type", "monitoring_events", ["event_type"])
    op.create_index("ix_monitoring_events_occurred_at", "monitoring_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("monitoring_events")
    op.drop_table("monitoring_alert_states")
    op.drop_table("monitoring_snapshots")
    op.drop_table("monitoring_policies")
