"""Evidence-only alert recovery verification and immutable evaluations."""
from alembic import op
import sqlalchemy as sa

revision = '20261010_32'
down_revision = '20261009_31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('recovery_verifications',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('alert_id', sa.String(36), sa.ForeignKey('alerts.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('active_alert_id', sa.String(36), sa.ForeignKey('alerts.id', ondelete='RESTRICT'), unique=True),
        sa.Column('kind', sa.String(16), nullable=False), sa.Column('metric', sa.String(32)),
        sa.Column('threshold', sa.Float()),
        sa.Column('probe_id', sa.String(36), sa.ForeignKey('http_probes.id', ondelete='RESTRICT')),
        sa.Column('config_snapshot', sa.JSON(), nullable=False),
        sa.Column('observation_minutes', sa.Integer(), nullable=False),
        sa.Column('max_gap_seconds', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False), sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)))
    for column in ('alert_id', 'asset_id', 'status'):
        op.create_index(f'ix_recovery_verifications_{column}', 'recovery_verifications', [column])
    op.create_table('recovery_evaluations', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('verification_id', sa.String(36), sa.ForeignKey('recovery_verifications.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('outcome', sa.String(16), nullable=False), sa.Column('reason_code', sa.String(64), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False), sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('trace_id', sa.String(36), nullable=False),
        sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False))
    for column in ('verification_id', 'evaluated_at'):
        op.create_index(f'ix_recovery_evaluations_{column}', 'recovery_evaluations', [column])
    with op.batch_alter_table('alert_notifications') as batch:
        batch.add_column(sa.Column('recovery_evaluation_id', sa.String(36)))
        batch.create_foreign_key('fk_notification_recovery', 'recovery_evaluations', ['recovery_evaluation_id'], ['id'], ondelete='RESTRICT')
        batch.create_index('ix_alert_notifications_recovery_evaluation_id', ['recovery_evaluation_id'])


def downgrade() -> None:
    with op.batch_alter_table('alert_notifications') as batch:
        batch.drop_index('ix_alert_notifications_recovery_evaluation_id')
        batch.drop_constraint('fk_notification_recovery', type_='foreignkey')
        batch.drop_column('recovery_evaluation_id')
    op.drop_table('recovery_evaluations')
    op.drop_table('recovery_verifications')
