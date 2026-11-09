"""Dedicated encrypted read-only Linux diagnostic authorization and audit."""
from alembic import op
import sqlalchemy as sa

revision = '20261007_30'
down_revision = '20261004_29'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('diagnostic_grants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='RESTRICT'), nullable=False, unique=True),
        sa.Column('host', sa.String(253), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('encrypted_secret', sa.LargeBinary(), nullable=False),
        sa.Column('nonce', sa.LargeBinary(12), nullable=False),
        sa.Column('key_version', sa.String(32), nullable=False),
        sa.Column('key_fingerprint', sa.String(128), nullable=False),
        sa.Column('host_fingerprint', sa.String(128), nullable=False),
        sa.Column('host_public_key', sa.Text(), nullable=False),
        sa.Column('host_key_type', sa.String(32), nullable=False),
        sa.Column('action_ids', sa.JSON(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_diagnostic_grants_asset_id', 'diagnostic_grants', ['asset_id'])
    op.create_table('diagnostic_grant_audits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('operator', sa.String(120), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_diagnostic_grant_audits_asset_id', 'diagnostic_grant_audits', ['asset_id'])


def downgrade() -> None:
    op.drop_table('diagnostic_grant_audits')
    op.drop_table('diagnostic_grants')
