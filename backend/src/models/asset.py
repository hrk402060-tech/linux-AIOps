from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Enum as SqlEnum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.credential import Credential


def _enum_values(items: type[Enum]) -> list[str]:
    return [str(item.value) for item in items]


class AssetStatus(str, Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"


class OsFamily(str, Enum):
    AUTO = "auto"
    UBUNTU = "ubuntu"
    DEBIAN = "debian"
    RHEL = "rhel"
    CENTOS = "centos"
    ROCKY = "rocky"
    ALMALINUX = "almalinux"
    OPENEULER = "openeuler"
    KYLIN = "kylin"
    UOS = "uos"
    ANOLIS = "anolis"
    OTHER = "other"


class AssetEnvironment(str, Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    TEST = "test"
    DEVELOPMENT = "development"
    OTHER = "other"


class AssetLifecycleStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    host: Mapped[str] = mapped_column(String(253), index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    os_family: Mapped[OsFamily] = mapped_column(
        SqlEnum(OsFamily, name="asset_os_family", values_callable=_enum_values)
    )
    environment: Mapped[AssetEnvironment] = mapped_column(
        SqlEnum(AssetEnvironment, name="asset_environment", values_callable=_enum_values)
    )
    group_name: Mapped[str] = mapped_column(String(100), default="未分组")
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[AssetStatus] = mapped_column(
        SqlEnum(AssetStatus, name="asset_status", values_callable=_enum_values),
        default=AssetStatus.UNKNOWN,
    )
    lifecycle_status: Mapped[AssetLifecycleStatus] = mapped_column(
        SqlEnum(
            AssetLifecycleStatus,
            name="asset_lifecycle_status",
            values_callable=_enum_values,
        ),
        default=AssetLifecycleStatus.ENABLED,
        index=True,
    )
    lifecycle_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lifecycle_operator: Mapped[str | None] = mapped_column(String(120), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    credential: Mapped["Credential | None"] = relationship(back_populates="assets")
