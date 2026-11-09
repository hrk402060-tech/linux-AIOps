"""Add service catalog and CMDB records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_22"
down_revision = "20260915_21"
branch_labels = None
depends_on = None

LIFECYCLE_VALUES = ("active", "disabled", "archived")
RELATION_VALUES = ("active", "unlinked")


def _enum(name: str, values: tuple[str, ...]) -> sa.Enum:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def _catalog_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "lifecycle_status", _enum("cmdb_lifecycle", LIFECYCLE_VALUES),
            nullable=False,
        ),
        sa.Column("lifecycle_reason", sa.String(255)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _catalog_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_code", table, ["code"])
    op.create_index(f"ix_{table}_name", table, ["name"])
    op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"])
    op.create_index(f"ix_{table}_lifecycle_status", table, ["lifecycle_status"])


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        postgresql.ENUM(*LIFECYCLE_VALUES, name="cmdb_lifecycle").create(
            op.get_bind(), checkfirst=True
        )
        postgresql.ENUM(*RELATION_VALUES, name="cmdb_service_asset_status").create(
            op.get_bind(), checkfirst=True
        )
    op.create_table(
        "cmdb_businesses",
        *_catalog_columns(),
        sa.UniqueConstraint("code", name="uq_cmdb_business_code"),
    )
    _catalog_indexes("cmdb_businesses")
    op.create_table(
        "cmdb_applications",
        *_catalog_columns(),
        sa.Column(
            "business_id", sa.String(36),
            sa.ForeignKey("cmdb_businesses.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.UniqueConstraint("business_id", "code", name="uq_cmdb_app_business_code"),
    )
    _catalog_indexes("cmdb_applications")
    op.create_index("ix_cmdb_applications_business_id", "cmdb_applications", ["business_id"])
    op.create_table(
        "cmdb_services",
        *_catalog_columns(),
        sa.Column(
            "application_id", sa.String(36),
            sa.ForeignKey("cmdb_applications.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.UniqueConstraint("application_id", "code", name="uq_cmdb_service_app_code"),
    )
    _catalog_indexes("cmdb_services")
    op.create_index("ix_cmdb_services_application_id", "cmdb_services", ["application_id"])
    op.create_index("ix_cmdb_services_service_type", "cmdb_services", ["service_type"])
    op.create_table(
        "cmdb_service_apis",
        *_catalog_columns(),
        sa.Column(
            "service_id", sa.String(36),
            sa.ForeignKey("cmdb_services.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("http_method", sa.String(12), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.UniqueConstraint("service_id", "code", name="uq_cmdb_api_service_code"),
    )
    _catalog_indexes("cmdb_service_apis")
    op.create_index("ix_cmdb_service_apis_service_id", "cmdb_service_apis", ["service_id"])
    op.create_table(
        "cmdb_service_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "service_id", sa.String(36),
            sa.ForeignKey("cmdb_services.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "asset_id", sa.String(36),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("relation_role", sa.String(32), nullable=False),
        sa.Column(
            "status", _enum("cmdb_service_asset_status", RELATION_VALUES),
            nullable=False,
        ),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("service_id", "asset_id", name="uq_cmdb_service_asset"),
    )
    for column in ("service_id", "asset_id", "status"):
        op.create_index(f"ix_cmdb_service_assets_{column}", "cmdb_service_assets", [column])
    op.create_table(
        "cmdb_change_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before_snapshot", sa.JSON()),
        sa.Column("after_snapshot", sa.JSON()),
        sa.Column(
            "operator_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("operator_username", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "entity_type", "entity_id", "action", "operator_user_id",
        "operator_username", "trace_id", "occurred_at",
    ):
        op.create_index(f"ix_cmdb_change_audits_{column}", "cmdb_change_audits", [column])


def downgrade() -> None:
    op.drop_table("cmdb_change_audits")
    op.drop_table("cmdb_service_assets")
    op.drop_table("cmdb_service_apis")
    op.drop_table("cmdb_services")
    op.drop_table("cmdb_applications")
    op.drop_table("cmdb_businesses")
    if op.get_bind().dialect.name == "postgresql":
        postgresql.ENUM(name="cmdb_service_asset_status").drop(
            op.get_bind(), checkfirst=True
        )
        postgresql.ENUM(name="cmdb_lifecycle").drop(op.get_bind(), checkfirst=True)
