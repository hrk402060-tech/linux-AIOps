import { apiFetch, parseApiResponse } from './client';

export interface ObservableService {
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

export interface ServiceObservabilityResponse {
  summary: { total: number; healthy: number; degraded: number; unavailable: number; unobserved: number };
  services: ObservableService[]; monitoring_mode: 'ssh_agentless_near_realtime';
  freshness_threshold_seconds: number;
}

export async function getServiceObservability(params: { query?: string; businessId?: string; status?: string } = {}): Promise<ServiceObservabilityResponse> {
  const search = new URLSearchParams();
  if (params.query?.trim()) search.set('query', params.query.trim());
  if (params.businessId) search.set('business_id', params.businessId);
  if (params.status) search.set('status', params.status);
  const response = await apiFetch(`/api/v1/observability/services?${search.toString()}`);
  return parseApiResponse<ServiceObservabilityResponse>(response, '服务可观测性加载失败');
}
