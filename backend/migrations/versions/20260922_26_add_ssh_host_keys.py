"""Add manually confirmed SSH host key registry and audit."""

from alembic import op
import sqlalchemy as sa

revision = "20260922_26"
down_revision = "20260918_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ssh_host_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "asset_id", sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("key_type", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(120), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", name="uq_ssh_host_keys_asset_id"),
    )
    for column in ("asset_id", "fingerprint"):
        op.create_index(f"ix_ssh_host_keys_{column}", "ssh_host_keys", [column])

    op.create_table(
        "ssh_host_key_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("asset_name", sa.String(100), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("key_type", sa.String(32), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("operator", sa.String(120), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("asset_id", "action", "occurred_at"):
        op.create_index(f"ix_ssh_host_key_audits_{column}", "ssh_host_key_audits", [column])


def downgrade() -> None:
    op.drop_table("ssh_host_key_audits")
    op.drop_table("ssh_host_keys")
