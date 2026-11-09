"""Add auditable CMDB service dependencies."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261107_47"
down_revision: str | Sequence[str] | None = "20261106_46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cmdb_service_dependencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_service_id",
            sa.String(36),
            sa.ForeignKey("cmdb_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_service_id",
            sa.String(36),
            sa.ForeignKey("cmdb_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dependency_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_cmdb_service_dependency_status",
        ),
        sa.UniqueConstraint(
            "source_service_id", "target_service_id", "dependency_type",
            name="uq_cmdb_service_dependency",
        ),
    )
    op.create_index(
        "ix_cmdb_service_dependencies_source",
        "cmdb_service_dependencies",
        ["source_service_id", "status"],
    )
    op.create_index(
        "ix_cmdb_service_dependencies_target",
        "cmdb_service_dependencies",
        ["target_service_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cmdb_service_dependencies_target",
        table_name="cmdb_service_dependencies",
    )
    op.drop_index(
        "ix_cmdb_service_dependencies_source",
        table_name="cmdb_service_dependencies",
    )
    op.drop_table("cmdb_service_dependencies")
