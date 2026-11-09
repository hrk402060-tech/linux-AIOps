"""add terminal assistant audit

Revision ID: 20260908_13
Revises: 20260908_12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260908_13"
down_revision: str | None = "20260908_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "terminal_assistant_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("terminal_session_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("redacted_context", sa.Text(), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("validation_steps", sa.JSON(), nullable=False),
        sa.Column("operator", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["terminal_session_id"], ["terminal_sessions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_terminal_assistant_analyses_terminal_session_id", "terminal_assistant_analyses", ["terminal_session_id"])
    op.create_index("ix_terminal_assistant_analyses_mode", "terminal_assistant_analyses", ["mode"])
    op.create_index("ix_terminal_assistant_analyses_operator", "terminal_assistant_analyses", ["operator"])
    op.create_table(
        "terminal_command_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("terminal_session_id", sa.String(36), nullable=False),
        sa.Column("command", sa.String(1000), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("validation_step", sa.String(500), nullable=False),
        sa.Column("risk_level", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("confirmation_phrase", sa.String(80), nullable=False),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["terminal_assistant_analyses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["terminal_session_id"], ["terminal_sessions.id"], ondelete="RESTRICT"),
    )
    for column in ("analysis_id", "terminal_session_id", "risk_level", "status"):
        op.create_index(f"ix_terminal_command_audits_{column}", "terminal_command_audits", [column])


def downgrade() -> None:
    op.drop_table("terminal_command_audits")
    op.drop_table("terminal_assistant_analyses")
