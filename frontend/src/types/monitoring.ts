export type ResourceState = 'unknown' | 'normal' | 'pending_warning' | 'warning' | 'pending_critical' | 'critical';
export type ResourceKey = 'cpu' | 'memory' | 'disk' | 'load';
export interface ThresholdPair { warning: number; critical: number }
export interface SeverityRules {
  cpu: ThresholdPair;
  memory: ThresholdPair;
  disk: ThresholdPair;
  load: ThresholdPair;
  duration_minutes: number;
}
export interface MonitorSnapshot {
  id: string;
  asset_id: string;
  status: string;
  read_only: boolean;
  metrics: Record<string, unknown>;
  error_message: string | null;
  collected_at: string;
}
export interface FleetAsset {
  asset_id: string;
  asset_name: string;
  host: string;
  group_name: string;
  lifecycle_status: string;
  latest_snapshot: MonitorSnapshot | null;
  stale: boolean;
  active_alerts: number;
  pending_vulnerabilities: number;
  critical: number;
  high: number;
  policy: { severity_rules: SeverityRules; [key: string]: unknown };
  resource_statuses: Record<ResourceKey, ResourceState>;
}
export interface FleetOverview {
  generated_at: string;
  stale_after_seconds: number;
  assets: FleetAsset[];
}
export interface FleetBucket {
  collected_at: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  cpu_asset_count: number;
  memory_asset_count: number;
}
export interface FleetTrends {
  generated_at: string;
  stale_after_seconds: number;
  bucket_seconds: number;
  buckets: FleetBucket[];
}
export interface NetworkInterface {
  name: string;
  rx_bytes_per_second: number | null;
  tx_bytes_per_second: number | null;
}
export interface DiskPartition {
  device: string;
  mount: string;
  total_bytes: number | null;
  used_bytes: number | null;
  available_bytes: number | null;
  used_percent: number | null;
  inode_percent: number | null;
}
