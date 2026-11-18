from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BasicSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    display_density: Literal["comfortable", "compact"] = "comfortable"
    default_page: Literal["dashboard", "server-monitoring"] = "dashboard"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("时区无效") from error
        return value


class ConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connect_timeout_seconds: int = Field(ge=5, le=120, strict=True)
    heartbeat_seconds: int = Field(ge=0, le=3600, strict=True)
    host_key_policy: Literal["accept_first_memory", "registered_only"] = "accept_first_memory"
    retry_count: int = Field(ge=0, le=5, strict=True)
    collection_interval_seconds: int = Field(ge=60, le=86400, strict=True)
    collection_scope: Literal["enabled_assets", "production_assets"] = "enabled_assets"


class TaskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrency: int = Field(ge=1, le=32, strict=True)
    terminal_max_connections: int = Field(ge=1, le=100, strict=True)
    execution_timeout_seconds: int = Field(ge=60, le=86400, strict=True)
    failure_policy: Literal["continue", "stop"] = "continue"
    terminal_idle_timeout_seconds: int = Field(ge=60, le=86400, strict=True)
    terminal_max_duration_seconds: int = Field(ge=300, le=86400, strict=True)
    require_confirmation: Literal[True] = True


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    default_channel: Literal["email", "webhook", "both"] = "email"
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = Field(default="23:00", pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str = Field(default="07:00", pattern=r"^\d{2}:\d{2}$")
    retry_count: int = Field(ge=0, le=10, strict=True)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_clock(cls, value: str) -> str:
        hour, minute = (int(item) for item in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("免打扰时间无效")
        return value


class AuthenticationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_login_enabled: bool = True
    ldap_enabled: bool = False
    oidc_enabled: bool = False
    new_account_policy: Literal["admin_only", "invite_only"] = "admin_only"

    @model_validator(mode="after")
    def require_login_source(self) -> "AuthenticationSettings":
        if not (self.local_login_enabled or self.ldap_enabled or self.oidc_enabled):
            raise ValueError("至少保留一种登录方式")
        return self


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(ge=7, le=3650, strict=True)
    auto_cleanup_enabled: bool = False
    backup_enabled: bool = False
    backup_path: str = Field(default="/var/lib/linux-ops/backups", min_length=1, max_length=255)
    verify_backup_after_create: Literal[True] = True
    backup_interval_hours: int = Field(default=24, ge=1, le=720, strict=True)
    cleanup_interval_hours: int = Field(default=24, ge=1, le=720, strict=True)

    @field_validator("backup_path")
    @classmethod
    def validate_backup_path(cls, value: str) -> str:
        from pathlib import PurePosixPath

        path = PurePosixPath(value)
        if (not path.is_absolute() or ".." in path.parts or str(path) == "/var/lib/linux-ops"
                or not path.is_relative_to("/var/lib/linux-ops")):
            raise ValueError("备份目录必须位于 /var/lib/linux-ops 下")
        return str(path)


class AIPolicySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops_assistant_enabled: bool = True
    vulnerability_assistant_enabled: bool = True
    include_terminal_output: bool = True
    include_asset_context: bool = True
    history_messages: int = Field(default=20, ge=0, le=20, strict=True)
    redact_sensitive_data: Literal[True] = True


SETTINGS_CATEGORIES = (
    "basic", "connection", "tasks", "notifications", "authentication", "storage",
)
