from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enum_values(items: type[Enum]) -> list[str]:
    return [str(item.value) for item in items]


class CmdbLifecycle(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ServiceAssetStatus(str, Enum):
    ACTIVE = "active"
    UNLINKED = "unlinked"


class ServiceDependencyStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class LifecycleColumns:
    lifecycle_status: Mapped[CmdbLifecycle] = mapped_column(
        SqlEnum(CmdbLifecycle, name="cmdb_lifecycle", values_callable=enum_values),
        default=CmdbLifecycle.ACTIVE,
        index=True,
    )
    lifecycle_reason: Mapped[str | None] = mapped_column(String(255))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CmdbBusiness(LifecycleColumns, Base):
    __tablename__ = "cmdb_businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class CmdbApplication(LifecycleColumns, Base):
    __tablename__ = "cmdb_applications"
    __table_args__ = (UniqueConstraint("business_id", "code", name="uq_cmdb_app_business_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    business_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_businesses.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class CmdbService(LifecycleColumns, Base):
    __tablename__ = "cmdb_services"
    __table_args__ = (UniqueConstraint("application_id", "code", name="uq_cmdb_service_app_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    application_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_applications.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    service_type: Mapped[str] = mapped_column(String(32), default="other", index=True)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class CmdbServiceApi(LifecycleColumns, Base):
    __tablename__ = "cmdb_service_apis"
    __table_args__ = (UniqueConstraint("service_id", "code", name="uq_cmdb_api_service_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    service_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_services.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    http_method: Mapped[str] = mapped_column(String(12), default="GET")
    path: Mapped[str] = mapped_column(String(500), default="/")
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class CmdbServiceAsset(Base):
    __tablename__ = "cmdb_service_assets"
    __table_args__ = (UniqueConstraint("service_id", "asset_id", name="uq_cmdb_service_asset"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    service_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_services.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    relation_role: Mapped[str] = mapped_column(String(32), default="member")
    status: Mapped[ServiceAssetStatus] = mapped_column(
        SqlEnum(ServiceAssetStatus, name="cmdb_service_asset_status", values_callable=enum_values),
        default=ServiceAssetStatus.ACTIVE,
        index=True,
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CmdbServiceDependency(Base):
    __tablename__ = "cmdb_service_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "source_service_id", "target_service_id", "dependency_type",
            name="uq_cmdb_service_dependency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_service_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_services.id", ondelete="RESTRICT"), index=True
    )
    target_service_id: Mapped[str] = mapped_column(
        ForeignKey("cmdb_services.id", ondelete="RESTRICT"), index=True
    )
    dependency_type: Mapped[str] = mapped_column(String(32), default="calls")
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ServiceDependencyStatus] = mapped_column(
        SqlEnum(
            ServiceDependencyStatus,
            name="cmdb_service_dependency_status",
            values_callable=enum_values,
        ),
        default=ServiceDependencyStatus.ACTIVE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CmdbChangeAudit(Base):
    __tablename__ = "cmdb_change_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(24), index=True)
    before_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    operator_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    operator_username: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
