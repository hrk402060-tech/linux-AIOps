"""Audited logical scan removal, independently deployable from production30."""
from alembic import op
import sqlalchemy as sa

revision = '20261012_31'
down_revision = '20261007_30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scan_task_removals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36),
                  sa.ForeignKey('scan_tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('operator_user_id', sa.String(36), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('evidence_snapshot', sa.JSON(), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', name='uq_scan_task_removal_task'),
    )
    op.create_index('ix_scan_task_removals_removed_at', 'scan_task_removals', ['removed_at'])


def downgrade() -> None:
    op.drop_index('ix_scan_task_removals_removed_at', table_name='scan_task_removals')
    op.drop_table('scan_task_removals')
