"""Add Docker read-only diagnostic transport metadata."""

import sqlalchemy as sa
from alembic import op

revision = '20261015_38'
down_revision = '20261015_37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('diagnostic_grants', sa.Column('docker_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM diagnostic_grants WHERE kind = 'docker'")):
        raise RuntimeError('Docker authorizations exist; downgrade would discard transport metadata')
    op.drop_column('diagnostic_grants', 'docker_config')
