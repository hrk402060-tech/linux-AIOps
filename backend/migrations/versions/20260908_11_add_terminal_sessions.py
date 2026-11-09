"""add terminal sessions

Revision ID: 20260908_11
Revises: 20260907_10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260908_11"
down_revision: str | None = "20260907_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terminal_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("bytes_in", sa.Integer(), nullable=False),
        sa.Column("bytes_out", sa.Integer(), nullable=False),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("ticket_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
    )
    for column in ("asset_id", "asset_name", "operator", "status", "requested_at"):
        op.create_index("ix_terminal_sessions_" + column, "terminal_sessions", [column])


def downgrade() -> None:
    for column in ("requested_at", "status", "operator", "asset_name", "asset_id"):
        op.drop_index("ix_terminal_sessions_" + column, table_name="terminal_sessions")
    op.drop_table("terminal_sessions")
