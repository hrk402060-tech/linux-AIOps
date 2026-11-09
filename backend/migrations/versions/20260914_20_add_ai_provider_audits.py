"""Add append-only AI provider configuration audits."""

from alembic import op
import sqlalchemy as sa

revision = '20260914_20'
down_revision = '20260911_19'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_provider_audits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'provider_id', sa.String(80),
            sa.ForeignKey('ai_provider_credentials.provider_id'),
            nullable=False,
        ),
        sa.Column('provider_name', sa.String(120), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_provider_audits_provider_id', 'ai_provider_audits', ['provider_id'])
    op.create_index('ix_ai_provider_audits_action', 'ai_provider_audits', ['action'])
    op.create_index('ix_ai_provider_audits_operator', 'ai_provider_audits', ['operator'])
    op.create_index('ix_ai_provider_audits_occurred_at', 'ai_provider_audits', ['occurred_at'])


def downgrade() -> None:
    op.drop_table('ai_provider_audits')
