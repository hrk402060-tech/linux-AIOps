"""Add explicit transport to the existing unique diagnostic authorization."""

import sqlalchemy as sa
from alembic import op

revision = '20261015_37'
down_revision = '20261014_36'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('diagnostic_grants', sa.Column('kind', sa.String(16), nullable=False,
                                                server_default='linux'))
    op.add_column('diagnostic_grants', sa.Column('kubernetes_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM diagnostic_grants WHERE kind <> 'linux'")):
        raise RuntimeError('Kubernetes authorizations exist; downgrade would discard transport metadata')
    op.drop_column('diagnostic_grants', 'kubernetes_config')
    op.drop_column('diagnostic_grants', 'kind')
