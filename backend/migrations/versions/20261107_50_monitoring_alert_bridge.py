"""Link monitoring threshold events to the shared alert workflow."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261107_50"
down_revision: str | Sequence[str] | None = "20261107_49"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("source_type", sa.String(32), nullable=True))
        batch.add_column(sa.Column("source_key", sa.String(220), nullable=True))
        batch.create_index("ix_alerts_source_type", ["source_type"])
        batch.create_index("ix_alerts_source_key", ["source_key"])


def downgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.drop_index("ix_alerts_source_key")
        batch.drop_index("ix_alerts_source_type")
        batch.drop_column("source_key")
        batch.drop_column("source_type")
