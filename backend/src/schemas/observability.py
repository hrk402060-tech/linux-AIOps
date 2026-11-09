from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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


class ServiceObservabilityResponse(BaseModel):
    summary: ObservabilitySummary
    services: list[ObservableService]
    monitoring_mode: Literal["ssh_agentless_near_realtime"]
    freshness_threshold_seconds: int
