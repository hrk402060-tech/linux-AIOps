"""Workspace-only merge; do not deploy pending recovery or diagnostic branches."""
revision = '20261013_34'
down_revision = ('20261012_33', '20261013_32')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
