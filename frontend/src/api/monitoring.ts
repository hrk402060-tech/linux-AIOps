import { apiFetch, parseApiResponse } from './client';
import type { FleetOverview, FleetTrends, MonitorSnapshot, SeverityRules } from '../types/monitoring';

async function request<T>(path: string, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  const timeout = AbortSignal.timeout(30_000);
  const response = await apiFetch(`/api/v1/monitoring${path}`, {
    ...init, signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  return parseApiResponse<T>(response, '监控数据读取失败');
}
export function getFleet(signal?: AbortSignal): Promise<FleetOverview> {
  return request('/fleet', signal);
}
export function getFleetTrends(assetIds: string[], hours: number, signal?: AbortSignal): Promise<FleetTrends> {
  const params = new URLSearchParams({ hours: String(hours) });
  assetIds.forEach((id) => params.append('asset_ids', id));
  return request(`/fleet/trends?${params}`, signal);
}
export function getMonitorHistory(assetId: string, hours: number, signal?: AbortSignal): Promise<MonitorSnapshot[]> {
  return request(`/assets/${encodeURIComponent(assetId)}/snapshots?limit=500&hours=${hours}`, signal);
}
export function collectMonitor(assetId: string): Promise<MonitorSnapshot> {
  return request(`/assets/${encodeURIComponent(assetId)}/collect`, undefined, { method: 'POST' });
}
export function saveMonitorRules(assetId: string, rules: SeverityRules): Promise<unknown> {
  return request(`/assets/${encodeURIComponent(assetId)}/policy`, undefined, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ severity_rules: rules }),
  });
}
