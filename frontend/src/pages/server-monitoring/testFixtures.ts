import type { FleetAsset, FleetOverview, FleetTrends, MonitorSnapshot, SeverityRules } from '../../types/monitoring';

export const rulesFixture: SeverityRules = {
  cpu: { warning: 70, critical: 90 }, memory: { warning: 70, critical: 90 },
  disk: { warning: 70, critical: 90 }, load: { warning: 0.7, critical: 1 }, duration_minutes: 5,
};
export function snapshotFixture(asset_id: string, metrics: Record<string, unknown> = {}): MonitorSnapshot {
  return { id: `${asset_id}-snapshot`, asset_id, status: 'online', read_only: true, metrics,
    error_message: null, collected_at: new Date().toISOString() };
}
export function assetFixture(asset_id: string, metrics: Record<string, unknown> = {}): FleetAsset {
  return { asset_id, asset_name: asset_id === 'a' ? 'GitLab' : 'Jenkins', host: `192.168.10.${asset_id === 'a' ? 11 : 12}`,
    group_name: asset_id === 'a' ? '生产' : '测试', lifecycle_status: 'enabled',
    latest_snapshot: snapshotFixture(asset_id, metrics), stale: false,
    active_alerts: 0, pending_vulnerabilities: 0, critical: 0, high: 0,
    policy: { severity_rules: rulesFixture },
    resource_statuses: { cpu: 'normal', memory: 'normal', disk: 'normal', load: 'normal' } };
}
export function fleetFixture(): FleetOverview {
  return { generated_at: new Date().toISOString(), stale_after_seconds: 900,
    assets: [assetFixture('a', { cpu_count: 8, cpu_percent: 20, memory_percent: 45, disk_percent: 30, load_1m: 1.2 }),
      { ...assetFixture('b', { cpu_percent: 5 }), latest_snapshot: { ...snapshotFixture('b'), status: 'offline' } }] };
}
export const trendsFixture: FleetTrends = {
  generated_at: new Date().toISOString(), bucket_seconds: 3600, stale_after_seconds: 900,
  buckets: [{ collected_at: new Date().toISOString(), cpu_percent: 20, memory_percent: 45,
    cpu_asset_count: 1, memory_asset_count: 1 }],
};
