import type { DiskPartition, FleetAsset, NetworkInterface, ResourceKey, ResourceState } from '../../types/monitoring';

export const stateLabels: Record<ResourceState, string> = {
  unknown: '未知', normal: '正常', pending_warning: '关注 · 持续确认中',
  warning: '关注', pending_critical: '超限 · 持续确认中', critical: '超限',
};
export const connectionLabels: Record<string, string> = {
  online: '在线', offline: '离线', failed: '采集失败', stale: '数据过期',
  disabled: '停用', uncollected: '未采集',
};
export function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}
export function numeric(value: unknown, max = Infinity): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max ? value : null;
}
export function connection(asset: FleetAsset): string {
  if (asset.lifecycle_status !== 'enabled') return 'disabled';
  if (!asset.latest_snapshot) return 'uncollected';
  if (asset.latest_snapshot.status === 'offline') return 'offline';
  if (asset.latest_snapshot.status !== 'online') return 'failed';
  return asset.stale ? 'stale' : 'online';
}
export function metric(asset: FleetAsset, key: string): number | null {
  if (key === 'disk_percent' && asset.latest_snapshot?.metrics.disk_observed === false) return null;
  return connection(asset) === 'online'
    ? numeric(asset.latest_snapshot?.metrics[key], key.endsWith('_percent') ? 100 : Infinity) : null;
}
export function coreCount(asset: FleetAsset): number | null {
  const count = metric(asset, 'cpu_count');
  return count && Number.isInteger(count) ? count : null;
}
export function displayState(asset: FleetAsset, key: ResourceKey): ResourceState {
  const fields = { cpu: 'cpu_percent', memory: 'memory_percent', disk: 'disk_percent', load: 'load_1m' };
  if (metric(asset, fields[key]) === null || (key === 'load' && coreCount(asset) === null)) return 'unknown';
  return asset.resource_statuses[key] ?? 'unknown';
}
export function tone(state: ResourceState): string {
  return state.includes('critical') ? 'critical' : state.includes('warning') ? 'warning' : state;
}
export function percent(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(1)}%`;
}
export function bytes(value: number | null): string {
  if (value === null) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = value === 0 ? 0 : Math.max(0, Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4));
  return `${(value / 1024 ** index).toFixed(index || (value > 0 && value < 1) ? 1 : 0)} ${units[index]}`;
}
export function rate(value: number | null): string {
  return value === null ? '—' : `${bytes(value)}/s`;
}
export function timestamp(value?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '—';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}
export function interfaces(metrics: Record<string, unknown>): NetworkInterface[] {
  if (!Array.isArray(metrics.network_interfaces)) return [];
  return metrics.network_interfaces.flatMap((value) => {
    const row = record(value);
    return row && typeof row.name === 'string' ? [{
      name: row.name, rx_bytes_per_second: numeric(row.rx_bytes_per_second),
      tx_bytes_per_second: numeric(row.tx_bytes_per_second),
    }] : [];
  });
}
export function partitions(metrics: Record<string, unknown>): DiskPartition[] {
  if (!Array.isArray(metrics.disks)) return [];
  return metrics.disks.flatMap((value) => {
    const row = record(value);
    return row && typeof row.mount === 'string' ? [{
      mount: row.mount, device: typeof row.device === 'string' ? row.device : '',
      total_bytes: numeric(row.total_bytes), used_bytes: numeric(row.used_bytes),
      available_bytes: numeric(row.available_bytes), used_percent: numeric(row.used_percent, 100),
      inode_percent: row.inode_observed === true ? numeric(row.inode_percent, 100) : null,
    }] : [];
  });
}
