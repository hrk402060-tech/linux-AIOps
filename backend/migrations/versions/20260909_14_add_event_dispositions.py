"""Add append-only event dispositions."""
from alembic import op
import sqlalchemy as sa

revision = '20260909_14'
down_revision = '20260908_13'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('event_dispositions',
                    sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('event_id', sa.String(180), nullable=False),
                    sa.Column('status', sa.String(30), nullable=False),
                    sa.Column('reason', sa.Text(), nullable=False),
                    sa.Column('operator', sa.String(100), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_event_dispositions_event_id', 'event_dispositions', ['event_id'])


def downgrade() -> None:
    op.drop_table('event_dispositions')
