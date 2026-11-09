import { apiFetch, parseApiResponse } from './client';

export interface ProbeResult {
  id: string;
  probe_id: string;
  status: 'healthy' | 'unhealthy' | 'error';
  http_status: number | null;
  latency_ms: number | null;
  checked_at: string;
  error_message: string | null;
  error_code: string | null;
  config_snapshot: { url: string; method: 'GET' | 'HEAD'; expected_status: number };
  operator_username: string;
  trace_id: string;
  business_id: string;
  application_id: string;
  service_id: string;
  asset_id: string;
  api_id: string | null;
  url: string;
  method: 'GET' | 'HEAD';
  expected_status: number;
}

export interface HttpProbe {
  id: string;
  name: string;
  service_id: string;
  service_name: string;
  asset_id: string;
  asset_name: string;
  api_id: string | null;
  url: string;
  method: 'GET' | 'HEAD';
  expected_status: number;
  timeout_seconds: number;
  enabled: boolean;
  created_at: string;
  latest_result: ProbeResult | null;
}

export interface ProbeCreate {
  name: string;
  service_id: string;
  asset_id: string;
  api_id?: string | null;
  url: string;
  method: 'GET' | 'HEAD';
  expected_status: number;
  timeout_seconds: number;
}

export interface ProbePolicy {
  allowed_cidrs: string[];
  configured?: boolean;
  source?: string;
  updated_at?: string | null;
}

const endpoint = '/api/v1/probes';
export function getProbePolicy(): Promise<ProbePolicy> {
  return request(`${endpoint}/policy`);
}
export function updateProbePolicy(allowedCidrs: string): Promise<ProbePolicy> {
  return request(`${endpoint}/policy`, {
    method: 'PUT',
    body: JSON.stringify({ allowed_cidrs: allowedCidrs }),
  });
}
function request<T>(url: string, options?: RequestInit): Promise<T> {
  return apiFetch(url, {
    ...options,
    headers: options?.body ? { 'Content-Type': 'application/json' } : undefined,
    signal: AbortSignal.timeout(20_000),
  }).then((response) => parseApiResponse<T>(response, '拨测请求失败'));
}
export function getProbes(serviceId?: string, page = 1): Promise<{ items: HttpProbe[]; total: number }> {
  const params = new URLSearchParams({ offset: String((page - 1) * 10), limit: '10' });
  if (serviceId) params.set('service_id', serviceId);
  return request<{ total: number; items: Array<HttpProbe & { status: string; latest_run: ProbeResult | null }> }>(`${endpoint}?${params}`).then((data) => ({ total: data.total, items: data.items.map((item) => ({ ...item, enabled: item.status === 'active', latest_result: item.latest_run ? { ...item.latest_run, ...item.latest_run.config_snapshot, error_message: item.latest_run.error_code } : null })) }));
}
export function createProbe(payload: ProbeCreate): Promise<HttpProbe> {
  return request(endpoint, { method: 'POST', body: JSON.stringify(payload) });
}
export function updateProbe(id: string, enabled: boolean): Promise<HttpProbe> {
  return request(`${endpoint}/${id}`, { method: 'PATCH', body: JSON.stringify({ status: enabled ? 'active' : 'disabled' }) });
}
export function runProbe(id: string): Promise<ProbeResult> {
  return request(`${endpoint}/${id}/run`, { method: 'POST' });
}
export function getProbeResults(id: string, page = 1): Promise<{ items: ProbeResult[]; total: number }> {
  return request<{ total: number; items: ProbeResult[] }>(`${endpoint}/${id}/runs?offset=${(page - 1) * 10}&limit=10`).then((data) => ({ total: data.total, items: data.items.map((item) => ({ ...item, ...item.config_snapshot, error_message: item.error_code })) }));
}
