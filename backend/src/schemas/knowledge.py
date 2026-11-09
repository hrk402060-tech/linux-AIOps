from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


Severity = Literal["critical", "high", "medium", "low", "unknown"]
OsFamily = Literal[
    "ubuntu", "debian", "rhel", "centos", "rocky", "almalinux",
    "openeuler", "kylin", "uos", "anolis", "other"
]


class AffectedPackage(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    affected_version: str = Field(min_length=1, max_length=200)
    fixed_version: str = Field(min_length=1, max_length=200)
    os_versions: list[str] = Field(default_factory=list, max_length=100)
    service_banner_keywords: list[str] = Field(default_factory=list, max_length=20)


class AdvisoryPackage(BaseModel):
    cve_id: str = Field(pattern=r"^CVE-[0-9]{4}-[0-9]{4,}$")
    advisory_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    severity: Severity
    os_family: OsFamily
    vendor_url: HttpUrl
    affected_packages: list[AffectedPackage] = Field(min_length=1, max_length=200)
    published_at: datetime
    updated_at: datetime


class OfflineKnowledgePackage(BaseModel):
    schema_version: Literal["1.0"]
    package_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    source_name: str = Field(min_length=1, max_length=100)
    published_at: datetime
    advisories: list[AdvisoryPackage] = Field(min_length=1, max_length=50000)

    @field_validator("advisories")
    @classmethod
    def unique_advisories(cls, value: list[AdvisoryPackage]) -> list[AdvisoryPackage]:
        identities = [(item.cve_id, item.advisory_id, item.os_family) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("离线包包含重复公告")
        return value


class KnowledgeImportRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    package_content: str = Field(min_length=1, max_length=70_000_000)
    signature: str = Field(min_length=1, max_length=1000)
    key_id: str = Field(min_length=1, max_length=100)


class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    provider: str
    sync_mode: str
    priority: int
    enabled: bool
    online_url: str | None
    schedule: str | None
    last_sync_at: datetime | None


class KnowledgeReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    version: str
    name: str
    package_id: str
    source_name: str
    signature_key_id: str
    content_sha256: str
    advisory_count: int
    status: str
    created_at: datetime
    activated_at: datetime | None


class KnowledgeAdvisoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    cve_id: str
    advisory_id: str
    title: str
    severity: str
    os_family: str
    source_name: str
    vendor_url: str
    affected_packages: list[AffectedPackage]
    published_at: datetime
    updated_at: datetime


class KnowledgeSyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_id: str | None
    source_name: str
    mode: str
    status: str
    filename: str | None
    signature_key_id: str | None
    content_sha256: str | None
    advisory_count: int
    message: str
    started_at: datetime
    finished_at: datetime | None


class KnowledgeSummaryResponse(BaseModel):
    active_version: str | None
    advisory_count: int
    source_count: int
    domestic_source_count: int
    last_updated_at: datetime | None


KnowledgeHealth = Literal["healthy", "stale", "failed", "never_synced"]


class KnowledgeCoverageSourceResponse(BaseModel):
    source_id: str
    source_name: str
    provider: str
    os_families: list[str]
    sync_mode: str
    advisory_count: int
    latest_advisory_at: datetime | None
    last_success_at: datetime | None
    latest_sync_status: str | None
    health: KnowledgeHealth
    is_stale: bool
    has_alert: bool
    stale_after_hours: int
    daily_schedule: str | None
    critical_check_interval_hours: int | None


class KnowledgeCoverageResponse(BaseModel):
    is_complete: Literal[False] = False
    completeness_note: str
    publication_policy: Literal["stage_validate_publish"]
    failure_policy: Literal["retain_active_release_and_alert"]
    critical_check_interval_hours: int
    sources: list[KnowledgeCoverageSourceResponse]


class KnowledgeBackfillCreate(BaseModel):
    batch_size: int = Field(default=10, ge=1, le=50)
    delay_seconds: int = Field(default=2, ge=0, le=30)


class KnowledgeBackfillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_id: str
    status: str
    current_page: int
    total_pages: int | None
    batch_size: int
    delay_seconds: int
    processed_notices: int
    published_advisories: int
    progress: int
    error_message: str | None
    last_release_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
