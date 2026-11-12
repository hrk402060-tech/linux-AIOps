from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ServiceHealth = Literal["healthy", "degraded", "unavailable", "unobserved"]
Freshness = Literal["fresh", "stale", "partial", "unobserved"]


class ObservabilitySummary(BaseModel):
    total: int
    healthy: int
    degraded: int
    unavailable: int
    unobserved: int


class ObservabilityAsset(BaseModel):
    asset_id: str
    asset_name: str
    host: str
    status: str
    freshness: Freshness
    collected_at: datetime | None


class ResourcePeaks(BaseModel):
    cpu_percent: float | None
    memory_percent: float | None
    disk_percent: float | None


class HealthReason(BaseModel):
    code: str
    message: str


class ServiceProbeSummary(BaseModel):
    configured: int = 0
    active: int = 0
    disabled: int = 0
    healthy: int = 0
    failed: int = 0
    unobserved: int = 0
    stale: int = 0
    api_count: int = 0
    covered_api_count: int = 0


class ServiceProbe(BaseModel):
    probe_id: str
    probe_name: str
    asset_id: str
    api_id: str | None
    status: Literal["healthy", "failed", "unobserved", "stale", "disabled"]
    reason_code: str | None
    checked_at: datetime | None
    latency_ms: int | None
    http_status: int | None


class ServiceAlert(BaseModel):
    alert_id: str | None
    asset_id: str
    source: Literal["monitoring", "external"]
    severity: str
    status: str
    message: str
    updated_at: datetime | None


class ObservableService(BaseModel):
    business_id: str
    business_name: str
    application_id: str
    application_name: str
    service_id: str
    service_name: str
    service_code: str
    service_type: str
    status: ServiceHealth
    freshness: Freshness
    asset_count: int
    online_assets: int
    offline_assets: int
    uncollected_assets: int
    disabled_assets: int
    stale_assets: int
    active_alerts: int
    pending_vulnerabilities: int
    critical: int
    high: int
    resource_peaks: ResourcePeaks
    last_collected_at: datetime | None
    assets: list[ObservabilityAsset]
    runtime_status: ServiceHealth = "unobserved"
    health_reasons: list[HealthReason] = Field(default_factory=list)
    health_scope: Literal["all", "authorized"] = "all"
    scope_complete: bool = True
    probe_summary: ServiceProbeSummary = Field(default_factory=ServiceProbeSummary)
    probes: list[ServiceProbe] = Field(default_factory=list)
    alerts: list[ServiceAlert] = Field(default_factory=list)


class ServiceObservabilityResponse(BaseModel):
    summary: ObservabilitySummary
    services: list[ObservableService]
    monitoring_mode: Literal["ssh_agentless_near_realtime"]
    freshness_threshold_seconds: int
