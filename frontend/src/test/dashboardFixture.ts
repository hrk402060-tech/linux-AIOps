import type { DashboardSnapshot } from '../types/dashboard';

export const dashboardFixture: DashboardSnapshot = {
  assets: { total: 4, online: 3, offline: 1, unknown: 0, disabled: 0 },
  vulnerabilities: { total: 0, critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0, pending: 0 },
  scans: { total: 0, queued: 0, running: 0, completed: 0, failed: 0, success_rate: 0 },
  knowledge: {
    active_version: null, active_advisory_count: 0, last_sync_status: null, last_sync_at: null,
    freshness: '未同步', stale: true, source_count: 0, healthy_source_count: 0,
    stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0,
  },
  recent_scans: [], affected_assets: [], resource_alerts: { cpu: 0, memory: 0, disk: 0 },
  monitoring_assets: [], resource_trends: [], active_alerts: [], attention_items: [],
  catalog: { businesses: 0, applications: 0, services: 0, apis: 0, linked_assets: 0, unlinked_assets: 0 },
  service_health: { summary: { total: 0, healthy: 0, degraded: 0, unavailable: 0, unobserved: 0 }, services: [] },
  operations: {
    active_alerts: 0, severe_alerts: 0, active_incidents: 0, severe_incidents: 0, running_tasks: 0,
    failed_tasks: 0, enabled_schedules: 0, active_probes: 0, failing_probes: 0, unobserved_probes: 0, pending_verification: 0,
  },
  monitoring_mode: 'ssh_agentless_near_realtime',
};
