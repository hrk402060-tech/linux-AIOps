"""Add encrypted user MFA configuration."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261031_45"
down_revision: str | Sequence[str] | None = "20261024_44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.add_column("users", sa.Column("mfa_secret_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("mfa_secret_nonce", sa.LargeBinary(length=12), nullable=True))
    op.add_column("users", sa.Column("mfa_key_version", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("mfa_recovery_code_hashes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mfa_recovery_code_hashes")
    op.drop_column("users", "mfa_key_version")
    op.drop_column("users", "mfa_secret_nonce")
    op.drop_column("users", "mfa_secret_encrypted")
    op.drop_column("users", "mfa_enabled")
