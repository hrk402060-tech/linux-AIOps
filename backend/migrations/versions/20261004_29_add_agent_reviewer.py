"""Record the authenticated reviewer without inventing legacy identities."""
from alembic import op
import sqlalchemy as sa

revision = "20261004_29"
down_revision = "20261003_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_recommendations", sa.Column("reviewed_by", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_recommendations", "reviewed_by")
