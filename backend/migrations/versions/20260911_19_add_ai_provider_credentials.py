from alembic import op
import sqlalchemy as sa

revision = '20260911_19'
down_revision = '20260909_18'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_provider_credentials',
        sa.Column('provider_id', sa.String(80), primary_key=True),
        sa.Column('provider_name', sa.String(120), nullable=False),
        sa.Column('encrypted_api_key', sa.LargeBinary(), nullable=True),
        sa.Column('nonce', sa.LargeBinary(12), nullable=True),
        sa.Column('key_version', sa.String(32), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('ai_provider_credentials')
