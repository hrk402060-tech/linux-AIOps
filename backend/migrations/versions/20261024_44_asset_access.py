"""Add scoped asset access and append-only user management audit."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261024_44"
down_revision: str | Sequence[str] | None = "20261024_43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts keep their former global scope; new accounts use ORM defaults.
    op.add_column("users", sa.Column(
        "asset_scope", sa.String(16), nullable=False, server_default="all"
    ))
    op.add_column("users", sa.Column(
        "access_revision", sa.Integer(), nullable=False, server_default="0"
    ))
    op.create_table(
        "user_asset_access",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("can_view", sa.Boolean(), nullable=False),
        sa.Column("can_ssh", sa.Boolean(), nullable=False),
        sa.Column("granted_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "asset_id", name="uq_user_asset_access"),
    )
    for column in ("user_id", "asset_id"):
        op.create_index(f"ix_user_asset_access_{column}", "user_asset_access", [column])
    op.create_table(
        "user_access_audits",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("operator_user_id", sa.String(36), nullable=True),
        sa.Column("operator_username", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "action", "operator_username", "occurred_at"):
        op.create_index(f"ix_user_access_audits_{column}", "user_access_audits", [column])


def downgrade() -> None:
    op.drop_table("user_access_audits")
    op.drop_table("user_asset_access")
    op.drop_column("users", "access_revision")
    op.drop_column("users", "asset_scope")
