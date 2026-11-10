from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.schemas.monitoring import MonitoringPolicyResponse, MonitoringSnapshotResponse

ResourceStatus = Literal[
    "unknown", "normal", "pending_warning", "warning", "pending_critical", "critical",
]


class MonitoringResourceStatuses(BaseModel):
    cpu: ResourceStatus
    memory: ResourceStatus
    disk: ResourceStatus
    load: ResourceStatus


class MonitoringFleetAsset(BaseModel):
    asset_id: str
    asset_name: str
    host: str
    group_name: str
    lifecycle_status: str
    latest_snapshot: MonitoringSnapshotResponse | None
    stale: bool
    active_alerts: int
    pending_vulnerabilities: int
    critical: int
    high: int
    policy: MonitoringPolicyResponse
    resource_statuses: MonitoringResourceStatuses


class MonitoringFleetResponse(BaseModel):
    generated_at: datetime
    stale_after_seconds: int
    assets: list[MonitoringFleetAsset]


class MonitoringFleetTrendBucket(BaseModel):
    collected_at: datetime
    cpu_percent: float | None
    memory_percent: float | None
    cpu_asset_count: int
    memory_asset_count: int


class MonitoringFleetTrendsResponse(BaseModel):
    generated_at: datetime
    stale_after_seconds: int
    bucket_seconds: int
    buckets: list[MonitoringFleetTrendBucket]
