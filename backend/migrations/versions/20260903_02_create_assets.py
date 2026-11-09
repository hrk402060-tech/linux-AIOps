"""Create managed Linux assets table."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260903_02"
down_revision: str | None = "20260902_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("os_family", _enum("asset_os_family", _os_values()), nullable=False),
        sa.Column("environment", _enum("asset_environment", _environment_values()), nullable=False),
        sa.Column("group_name", sa.String(length=100), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("status", _enum("asset_status", ("unknown", "online", "offline")), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("check_message", sa.String(length=255), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_assets_name"),
    )
    op.create_index("ix_assets_name", "assets", ["name"], unique=True)
    op.create_index("ix_assets_host", "assets", ["host"], unique=False)
    op.create_index("ix_assets_credential_id", "assets", ["credential_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_assets_credential_id", table_name="assets")
    op.drop_index("ix_assets_host", table_name="assets")
    op.drop_index("ix_assets_name", table_name="assets")
    op.drop_table("assets")
    for name in ("asset_status", "asset_environment", "asset_os_family"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)


def _enum(name: str, values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=name)


def _os_values() -> tuple[str, ...]:
    return ("auto", "ubuntu", "debian", "rhel", "centos", "rocky", "almalinux", "openeuler", "kylin", "uos", "anolis", "other")


def _environment_values() -> tuple[str, ...]:
    return ("production", "staging", "test", "development", "other")
