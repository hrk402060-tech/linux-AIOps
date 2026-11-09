"""Durable read-only diagnostic jobs, admission lock and global worker slots."""
from alembic import op
import sqlalchemy as sa

revision = '20261009_31'
down_revision = '20261007_30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('diagnostic_admission', sa.Column('id', sa.Integer(), primary_key=True),
                    sa.Column('revision', sa.Integer(), nullable=False))
    op.create_table('diagnostic_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('alert_id', sa.String(36), sa.ForeignKey('alerts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('active_alert_id', sa.String(36), sa.ForeignKey('alerts.id', ondelete='RESTRICT'), unique=True),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('grant_revision', sa.Integer()),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('trigger_type', sa.String(16), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('current_analysis_id', sa.String(36)),
        sa.Column('lease_token', sa.String(36)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('cancellation_requested_at', sa.DateTime(timezone=True)),
        sa.Column('last_error_code', sa.String(80)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)))
    for column in ('alert_id', 'asset_id', 'status', 'next_attempt_at'):
        op.create_index(f'ix_diagnostic_jobs_{column}', 'diagnostic_jobs', [column])
    op.create_table('diagnostic_slots', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('diagnostic_jobs.id', ondelete='RESTRICT')),
        sa.Column('lease_token', sa.String(36)), sa.Column('lease_expires_at', sa.DateTime(timezone=True)))
    with op.batch_alter_table('root_cause_analyses') as batch:
        batch.add_column(sa.Column('job_id', sa.String(36)))
        batch.add_column(sa.Column('attempt_number', sa.Integer()))
        batch.create_foreign_key('fk_analysis_diagnostic_job', 'diagnostic_jobs', ['job_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_root_cause_analyses_job_id', ['job_id'])
    op.bulk_insert(sa.table('diagnostic_admission', sa.column('id', sa.Integer()), sa.column('revision', sa.Integer())),
                   [{'id': 1, 'revision': 0}])
    op.bulk_insert(sa.table('diagnostic_slots', sa.column('id', sa.Integer())), [{'id': 1}, {'id': 2}])


def downgrade() -> None:
    with op.batch_alter_table('root_cause_analyses') as batch:
        batch.drop_index('ix_root_cause_analyses_job_id')
        batch.drop_constraint('fk_analysis_diagnostic_job', type_='foreignkey')
        batch.drop_column('attempt_number')
        batch.drop_column('job_id')
    op.drop_table('diagnostic_slots')
    op.drop_table('diagnostic_jobs')
    op.drop_table('diagnostic_admission')
