"""Add persistent platform settings."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261024_43"
down_revision: str | Sequence[str] | None = "20261022_42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.String(length=120), nullable=False, server_default="system"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
