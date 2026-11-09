export interface DashboardMonitoringAsset {
  asset_id: string; asset_name: string; host: string; lifecycle_status: string;
  connection_status: string; metrics: Record<string, unknown>; collected_at: string | null;
  stale: boolean; active_alerts: number; critical: number; high: number;
  pending_vulnerabilities: number; recent_scan_at: string | null;
  cmdb_services: DashboardServiceContext[];
}

export interface DashboardServiceContext {
  business_id: string; business_name: string;
  application_id: string; application_name: string;
  service_id: string; service_name: string;
  service_type: string; relation_role: string;
}

export interface DashboardCatalog {
  businesses: number; applications: number; services: number; apis: number;
  linked_assets: number; unlinked_assets: number;
}

export interface DashboardServiceHealthItem {
  business_id: string; business_name: string; application_id: string; application_name: string;
  service_id: string; service_name: string; service_code: string; service_type: string;
  status: 'healthy' | 'degraded' | 'unavailable' | 'unobserved'; asset_count: number;
  freshness: 'fresh' | 'stale' | 'partial' | 'unobserved'; stale_assets: number;
  online_assets: number; offline_assets: number; uncollected_assets: number; disabled_assets: number;
  active_alerts: number; pending_vulnerabilities: number; critical: number; high: number;
  resource_peaks: { cpu_percent: number | null; memory_percent: number | null; disk_percent: number | null };
  last_collected_at: string | null;
  assets: Array<{
    asset_id: string; asset_name: string; host: string; status: string;
    freshness: 'fresh' | 'stale' | 'partial' | 'unobserved'; collected_at: string | null;
  }>;
}

export interface DashboardServiceHealth {
  summary: { total: number; healthy: number; degraded: number; unavailable: number; unobserved: number };
  services: DashboardServiceHealthItem[];
}

export interface DashboardOperations {
  active_alerts: number; severe_alerts: number; active_incidents: number; severe_incidents: number;
  running_tasks: number; failed_tasks: number; enabled_schedules: number; active_probes: number;
  failing_probes: number; unobserved_probes: number; pending_verification: number;
}

export interface DashboardAttentionItem {
  id: string; kind: string; title: string; detail: string; severity: string; status: string;
  occurred_at: string | null; asset_id: string | null; asset_name: string | null; target: string;
}

export interface DashboardSnapshot {
  assets: { total: number; online: number; offline: number; unknown: number; disabled: number };
  vulnerabilities: {
    total: number; critical: number; high: number; medium: number; low: number;
    confirmed: number; manual_confirmation: number; pending: number;
  };
  scans: { total: number; queued: number; running: number; completed: number; failed: number; success_rate: number };
  knowledge: {
    active_version: string | null; active_advisory_count: number;
    last_sync_status: string | null; last_sync_at: string | null; freshness: string; stale: boolean;
    source_count: number; healthy_source_count: number; stale_source_count: number;
    failed_source_count: number; never_synced_source_count: number;
  };
  recent_scans: Array<{
    id: string; name: string; scan_type: string; status: string; progress: number;
    finding_count: number; created_at: string;
  }>;
  affected_assets: Array<{ asset_id: string; asset_name: string; total: number; critical: number; high: number }>;
  resource_alerts: { cpu: number; memory: number; disk: number };
  monitoring_assets: DashboardMonitoringAsset[];
  resource_trends: Array<{ collected_at: string; cpu_percent: number; memory_percent: number; disk_percent: number }>;
  active_alerts: Array<{ asset_id: string; asset_name: string; metric: string; value: number; threshold: number; triggered_at: string | null }>;
  catalog: DashboardCatalog;
  service_health: DashboardServiceHealth;
  operations: DashboardOperations;
  attention_items: DashboardAttentionItem[];
  monitoring_mode: 'ssh_agentless_near_realtime';
}
