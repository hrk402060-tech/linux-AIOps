import { apiFetch } from './client';

export interface SecurityEvent {
  id: string; title: string; asset_id: string | null; asset_name: string;
  severity: string; source: string; status: string; recovered: boolean; occurred_at: string;
}
export interface EventAudit { status: string; reason: string; operator: string; created_at: string }
export interface MonitoringPolicy {
  asset_id: string; cpu_threshold: number; memory_threshold: number; swap_threshold: number;
  disk_threshold: number; inode_threshold: number; load_threshold: number; duration_minutes: number;
  recovery_notify: boolean; maintenance_start: string | null; maintenance_end: string | null;
}
export interface MonitoringOverview { assets: Array<{ asset_id: string; asset_name: string; policy: MonitoringPolicy }> }
export async function eventRequest<T>(url: string, payload?: object, method = 'POST'): Promise<T> {
  const response = await apiFetch(url, {
    signal: AbortSignal.timeout(15000),
    method: payload ? method : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (!response.ok) throw new Error('事件请求失败，请检查权限或重试');
  return response.json() as Promise<T>;
}

export const getMonitoringOverview = () =>
  eventRequest<MonitoringOverview>('/api/v1/monitoring/overview');

export const updateMonitoringPolicy = (assetId: string, policy: Partial<MonitoringPolicy>) =>
  eventRequest<MonitoringPolicy>(`/api/v1/monitoring/assets/${assetId}/policy`, policy, 'PATCH');
