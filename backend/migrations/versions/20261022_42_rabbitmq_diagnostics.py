"""Add RabbitMQ read-only diagnostic configuration."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261022_42"
down_revision: str | Sequence[str] | None = "20261022_41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diagnostic_grants", sa.Column("rabbitmq_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM diagnostic_grants WHERE kind = 'rabbitmq' LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while RabbitMQ diagnostic grants exist")
    op.drop_column("diagnostic_grants", "rabbitmq_config")
