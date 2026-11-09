from datetime import datetime
from ipaddress import ip_address
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.asset import AssetEnvironment, AssetLifecycleStatus, AssetStatus, OsFamily
from src.models.credential import CredentialType

HOST_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


class CredentialSummary(BaseModel):
    id: str
    name: str
    username: str
    credential_type: CredentialType

    model_config = ConfigDict(from_attributes=True)


class AssetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    os_family: OsFamily = OsFamily.AUTO
    environment: AssetEnvironment = AssetEnvironment.PRODUCTION
    group_name: str = Field(default="未分组", min_length=1, max_length=100)
    credential_id: str | None = None
    ssh_username: str | None = Field(default=None, min_length=1, max_length=64)
    ssh_credential_type: CredentialType = CredentialType.PASSWORD
    ssh_secret: str | None = Field(default=None, min_length=1, max_length=16384)
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name", "group_name", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("host", mode="before")
    @classmethod
    def validate_host(cls, value: str) -> str:
        host = value.strip()
        try:
            ip_address(host)
            return host
        except ValueError:
            labels = host.rstrip(".").split(".")
            if not labels or not all(HOST_LABEL.fullmatch(label) for label in labels):
                raise ValueError("Host must be a valid IP address or DNS hostname")
            return host.rstrip(".").lower()

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if any(len(value) > 32 for value in normalized):
            raise ValueError("Labels must contain at most 32 characters")
        return list(dict.fromkeys(normalized))


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    os_family: OsFamily | None = None
    environment: AssetEnvironment | None = None
    group_name: str | None = Field(default=None, min_length=1, max_length=100)
    credential_id: str | None = None
    ssh_username: str | None = Field(default=None, min_length=1, max_length=64)
    ssh_credential_type: CredentialType | None = None
    ssh_secret: str | None = Field(default=None, min_length=1, max_length=16384)
    labels: list[str] | None = Field(default=None, max_length=20)

    _strip_text = field_validator("name", "group_name", mode="before")(
        AssetCreate.strip_text.__func__
    )
    _validate_host = field_validator("host", mode="before")(
        AssetCreate.validate_host.__func__
    )
    _normalize_labels = field_validator("labels")(AssetCreate.normalize_labels.__func__)


class AssetResponse(BaseModel):
    id: str
    name: str
    host: str
    ssh_port: int
    os_family: OsFamily
    environment: AssetEnvironment
    group_name: str
    credential_id: str | None
    credential: CredentialSummary | None
    labels: list[str]
    status: AssetStatus
    lifecycle_status: AssetLifecycleStatus
    lifecycle_reason: str | None
    lifecycle_operator: str | None
    archived_at: datetime | None
    latency_ms: int | None
    check_message: str | None
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetLifecycleUpdate(BaseModel):
    status: AssetLifecycleStatus
    reason: str = Field(min_length=2, max_length=255)
    operator: str = Field(default="authenticated", min_length=2, max_length=120)


class AssetCleanupRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    operator: str = Field(default="authenticated", min_length=2, max_length=120)
    confirmation: str = Field(min_length=1, max_length=100)


class AssetCleanupPreview(BaseModel):
    asset_id: str
    allowed: bool
    reason: str | None
    confirmation_phrase: str
    scan_tasks: int
    shared_scan_tasks: int
    vulnerability_findings: int
    vulnerability_cases: int
    reports: int
    monitoring_records: int
    terminal_sessions: int
    alert_records: int
    agent_recommendations: int


class AssetCleanupAuditResponse(BaseModel):
    id: str
    asset_id: str
    asset_name: str
    environment: str
    reason: str
    operator: str
    evidence_snapshot: dict[str, object]
    deleted_at: datetime

    model_config = ConfigDict(from_attributes=True)
