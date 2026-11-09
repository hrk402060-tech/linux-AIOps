"""Create versioned vulnerability knowledge base.

Revision ID: 20260903_03
Revises: 20260903_02
Create Date: 2026-09-03
"""

from collections.abc import Sequence
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "20260903_03"
down_revision: str | None = "20260903_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("sync_mode", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("online_url", sa.String(500), nullable=True),
        sa.Column("schedule", sa.String(80), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_knowledge_sources_name"),
    )
    op.create_index("ix_knowledge_sources_name", "knowledge_sources", ["name"], unique=True)
    op.create_index("ix_knowledge_sources_sync_mode", "knowledge_sources", ["sync_mode"])
    source_table = sa.table(
        "knowledge_sources",
        sa.column("id", sa.String), sa.column("name", sa.String),
        sa.column("provider", sa.String), sa.column("sync_mode", sa.String),
        sa.column("priority", sa.Integer), sa.column("enabled", sa.Boolean),
        sa.column("online_url", sa.String), sa.column("schedule", sa.String),
        sa.column("last_sync_at", sa.DateTime), sa.column("created_at", sa.DateTime),
    )
    now = datetime.now(timezone.utc)
    rows = (
        ("10000000-0000-0000-0000-000000000001", "Ubuntu Security Notices", "Canonical", "online_secondary", 10, "0 3 * * *"),
        ("10000000-0000-0000-0000-000000000002", "Debian Security Advisories", "Debian", "online_secondary", 10, "15 3 * * *"),
        ("10000000-0000-0000-0000-000000000003", "Red Hat Security Advisories", "Red Hat", "online_secondary", 10, "30 3 * * *"),
        ("10000000-0000-0000-0000-000000000004", "openEuler Security Advisories", "openEuler", "online_secondary", 10, "45 3 * * *"),
        ("10000000-0000-0000-0000-000000000005", "Kylin Security Advisories", "Kylinsoft", "offline_package", 10, None),
        ("10000000-0000-0000-0000-000000000006", "UnionTech Security Advisories", "UnionTech", "offline_package", 10, None),
        ("10000000-0000-0000-0000-000000000007", "Anolis Security Advisories", "OpenAnolis", "online_secondary", 10, "0 4 * * *"),
        ("10000000-0000-0000-0000-000000000008", "CNVD", "CNCERT", "offline_package", 50, None),
        ("10000000-0000-0000-0000-000000000009", "CNNVD", "CNNVD", "offline_package", 50, None),
    )
    op.bulk_insert(source_table, [{
        "id": item[0], "name": item[1], "provider": item[2],
        "sync_mode": item[3], "priority": item[4], "enabled": True,
        "online_url": None, "schedule": item[5], "last_sync_at": None,
        "created_at": now,
    } for item in rows])
    op.create_table(
        "knowledge_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("package_id", sa.String(120), nullable=False),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("signature_key_id", sa.String(100), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("advisory_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("version", name="uq_knowledge_releases_version"),
    )
    op.create_index("ix_knowledge_releases_version", "knowledge_releases", ["version"], unique=True)
    op.create_index("ix_knowledge_releases_status", "knowledge_releases", ["status"])
    op.create_index(
        "uq_knowledge_releases_one_active", "knowledge_releases", ["status"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "knowledge_advisories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("cve_id", sa.String(32), nullable=False),
        sa.Column("advisory_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("os_family", sa.String(30), nullable=False),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("vendor_url", sa.String(500), nullable=False),
        sa.Column("affected_packages", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["knowledge_releases.id"], ondelete="CASCADE"),
    )
    for column in ("release_id", "cve_id", "advisory_id", "severity", "os_family"):
        op.create_index(f"ix_knowledge_advisories_{column}", "knowledge_advisories", [column])
    op.create_table(
        "knowledge_sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=True),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("signature_key_id", sa.String(100), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("advisory_count", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_knowledge_sync_runs_status", "knowledge_sync_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_sync_runs_status", table_name="knowledge_sync_runs")
    op.drop_table("knowledge_sync_runs")
    for column in ("os_family", "severity", "advisory_id", "cve_id", "release_id"):
        op.drop_index(f"ix_knowledge_advisories_{column}", table_name="knowledge_advisories")
    op.drop_table("knowledge_advisories")
    op.drop_index("uq_knowledge_releases_one_active", table_name="knowledge_releases")
    op.drop_index("ix_knowledge_releases_status", table_name="knowledge_releases")
    op.drop_index("ix_knowledge_releases_version", table_name="knowledge_releases")
    op.drop_table("knowledge_releases")
    op.drop_index("ix_knowledge_sources_sync_mode", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_name", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
