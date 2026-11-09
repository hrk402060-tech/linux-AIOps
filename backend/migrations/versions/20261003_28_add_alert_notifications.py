"""Durable SMTP administrator notification channel and delivery evidence."""
from alembic import op
import sqlalchemy as sa

revision = '20261003_28'
down_revision = '20260925_27'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('alert_notification_config',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('host', sa.String(253), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('security', sa.String(16), nullable=False),
        sa.Column('username', sa.String(200), nullable=False),
        sa.Column('sender', sa.String(254), nullable=False),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('encrypted_password', sa.LargeBinary()),
        sa.Column('password_nonce', sa.LargeBinary(12)),
        sa.Column('key_version', sa.String(32)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table('alert_notifications',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dedupe_key', sa.String(200), nullable=False, unique=True),
        sa.Column('alert_id', sa.String(36), sa.ForeignKey('alerts.id', ondelete='RESTRICT')),
        sa.Column('analysis_id', sa.String(36), sa.ForeignKey('root_cause_analyses.id', ondelete='RESTRICT')),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('source', sa.String(120)),
        sa.Column('external_id', sa.String(180)),
        sa.Column('asset_locator', sa.JSON(), nullable=False),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(80)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True)),
        sa.Column('acknowledged_by', sa.String(120)),
        sa.Column('claim_token', sa.String(36)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
    )
    for field in ('alert_id', 'analysis_id', 'kind', 'status', 'created_at', 'next_attempt_at', 'lease_expires_at'):
        op.create_index(f'ix_alert_notifications_{field}', 'alert_notifications', [field])
    op.create_table('alert_notification_receipts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('connector_id', sa.String(36), sa.ForeignKey('alert_connectors.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('nonce_hash', sa.String(64), nullable=False),
        sa.Column('external_id', sa.String(180), nullable=False),
        sa.Column('notification_id', sa.String(36), sa.ForeignKey('alert_notifications.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('connector_id', 'nonce_hash', name='uq_notification_receipt_nonce'),
    )
    op.create_index('ix_alert_notification_receipts_connector_id', 'alert_notification_receipts', ['connector_id'])
    op.create_table('alert_notification_audits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('notification_id', sa.String(36), sa.ForeignKey('alert_notifications.id', ondelete='RESTRICT')),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('detail', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    for field in ('notification_id', 'action', 'operator', 'created_at'):
        op.create_index(f'ix_alert_notification_audits_{field}', 'alert_notification_audits', [field])


def downgrade() -> None:
    op.drop_table('alert_notification_audits')
    op.drop_table('alert_notification_receipts')
    op.drop_table('alert_notifications')
    op.drop_table('alert_notification_config')
