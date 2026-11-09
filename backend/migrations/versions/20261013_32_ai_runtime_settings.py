"""Add administrator-managed AI runtime configuration, preserving existing credentials."""
from alembic import op
import sqlalchemy as sa

revision = '20261013_32'
down_revision = '20261012_31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ai_provider_credentials', sa.Column('base_url', sa.String(1000), nullable=False, server_default=''))
    op.add_column('ai_provider_credentials', sa.Column('model', sa.String(200), nullable=False, server_default=''))
    op.add_column('ai_provider_credentials', sa.Column('timeout_seconds', sa.Integer(), nullable=False, server_default='30'))


def downgrade() -> None:
    op.drop_column('ai_provider_credentials', 'timeout_seconds')
    op.drop_column('ai_provider_credentials', 'model')
    op.drop_column('ai_provider_credentials', 'base_url')
