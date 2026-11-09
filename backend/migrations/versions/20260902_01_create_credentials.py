"""Create encrypted SSH credentials table."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260902_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    credential_type = sa.Enum("password", "private_key", name="credential_type")
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("credential_type", credential_type, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_credentials_name"),
    )
    op.create_index("ix_credentials_name", "credentials", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_credentials_name", table_name="credentials")
    op.drop_table("credentials")
    sa.Enum(name="credential_type").drop(op.get_bind(), checkfirst=True)

