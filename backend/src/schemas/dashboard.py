from datetime import datetime

from pydantic import BaseModel

from src.schemas.observability import ObservableService, ObservabilitySummary


class DashboardAssets(BaseModel):
    total: int
    online: int
    offline: int
    unknown: int
    disabled: int


class DashboardVulnerabilities(BaseModel):
    total: int
    critical: int
    high: int
    medium: int
    low: int
    confirmed: int
    manual_confirmation: int
    pending: int


class DashboardScans(BaseModel):
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    success_rate: int


class DashboardKnowledge(BaseModel):
    active_version: str | None
    active_advisory_count: int
    last_sync_status: str | None
    last_sync_at: datetime | None
    freshness: str
    stale: bool
    source_count: int
    healthy_source_count: int
    stale_source_count: int
    failed_source_count: int
    never_synced_source_count: int


class DashboardResourceAlerts(BaseModel):
    cpu: int
    memory: int
    disk: int


class DashboardMonitoringAsset(BaseModel):
    asset_id: str
    asset_name: str
    host: str
    lifecycle_status: str
    connection_status: str
    metrics: dict[str, object]
    collected_at: datetime | None
    stale: bool
    active_alerts: int
    critical: int
    high: int
    pending_vulnerabilities: int
    recent_scan_at: datetime | None
    cmdb_services: list["DashboardServiceContext"]


class DashboardServiceContext(BaseModel):
    business_id: str
    business_name: str
    application_id: str
    application_name: str
    service_id: str
    service_name: str
    service_type: str
    relation_role: str


class DashboardTrendPoint(BaseModel):
    collected_at: datetime
    cpu_percent: float
    memory_percent: float
    disk_percent: float


class DashboardActiveAlert(BaseModel):
    asset_id: str
    asset_name: str
    metric: str
    value: float
    threshold: float
    triggered_at: datetime | None


class RecentScan(BaseModel):
    id: str
    name: str
    scan_type: str
    status: str
    progress: int
    finding_count: int
    created_at: datetime


class AffectedAsset(BaseModel):
    asset_id: str
    asset_name: str
    total: int
    critical: int
    high: int


class DashboardCatalog(BaseModel):
    businesses: int
    applications: int
    services: int
    apis: int
    linked_assets: int
    unlinked_assets: int


class DashboardServiceHealth(BaseModel):
    summary: ObservabilitySummary
    services: list[ObservableService]


class DashboardOperations(BaseModel):
    active_alerts: int
    severe_alerts: int
    active_incidents: int
    severe_incidents: int
    running_tasks: int
    failed_tasks: int
    enabled_schedules: int
    active_probes: int
    failing_probes: int
    unobserved_probes: int
    pending_verification: int


class DashboardAttentionItem(BaseModel):
    id: str
    kind: str
    title: str
    detail: str
    severity: str
    status: str
    occurred_at: datetime | None
    asset_id: str | None
    asset_name: str | None
    target: str


class DashboardResponse(BaseModel):
    assets: DashboardAssets
    vulnerabilities: DashboardVulnerabilities
    scans: DashboardScans
    knowledge: DashboardKnowledge
    recent_scans: list[RecentScan]
    affected_assets: list[AffectedAsset]
    resource_alerts: DashboardResourceAlerts
    monitoring_assets: list[DashboardMonitoringAsset]
    resource_trends: list[DashboardTrendPoint]
    active_alerts: list[DashboardActiveAlert]
    catalog: DashboardCatalog
    service_health: DashboardServiceHealth
    operations: DashboardOperations
    attention_items: list[DashboardAttentionItem]
    monitoring_mode: str
