"""Persist per-user read-only operations assistant conversations."""

import sqlalchemy as sa
from alembic import op

revision = "20261021_39"
down_revision = "20261015_38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_assistant_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("redacted_context", sa.Text(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("active_request_id", sa.String(36), nullable=True),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ops_conversations_owner_archived_updated",
        "ops_assistant_conversations", ["owner_key", "archived", "updated_at"],
    )
    op.create_table(
        "ops_assistant_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("ops_assistant_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "number", name="uq_ops_message_number"),
        sa.UniqueConstraint("conversation_id", "request_id", "role", name="uq_ops_message_request_role"),
    )
    op.create_index("ix_ops_messages_conversation_number", "ops_assistant_messages", ["conversation_id", "number"])


def downgrade() -> None:
    for table in ("ops_assistant_conversations", "ops_assistant_messages"):
        if op.get_bind().scalar(sa.select(sa.func.count()).select_from(sa.table(table))):
            raise RuntimeError("Conversation data exists; downgrade would discard history")
    op.drop_table("ops_assistant_messages")
    op.drop_table("ops_assistant_conversations")
