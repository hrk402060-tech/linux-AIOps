"""Add optional display severity rules without changing notification policies."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261110_51"
down_revision: str | Sequence[str] | None = "20261107_50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("monitoring_policies") as batch:
        batch.add_column(sa.Column("severity_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("monitoring_policies") as batch:
        batch.drop_column("severity_rules")
