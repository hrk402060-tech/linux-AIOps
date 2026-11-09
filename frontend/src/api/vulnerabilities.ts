import { apiFetch } from './client';
import type {
  VulnerabilityConclusion, VulnerabilityDispositionFilter, VulnerabilityDispositionUpdate,
  VulnerabilityFinding, VulnerabilityListResponse, VulnerabilitySeverity,
} from '../types/vulnerability';

const endpoint = '/api/v1/vulnerabilities';

export interface VulnerabilityFilters {
  query?: string;
  severity?: VulnerabilitySeverity;
  conclusion?: VulnerabilityConclusion;
  assetId?: string;
  disposition?: VulnerabilityDispositionFilter;
  page?: number;
  pageSize?: number;
}

export async function getVulnerabilities(
  filters: VulnerabilityFilters = {},
): Promise<VulnerabilityListResponse> {
  const parameters = new URLSearchParams();
  if (filters.query) parameters.set('query', filters.query);
  if (filters.severity) parameters.set('severity', filters.severity);
  if (filters.conclusion) parameters.set('conclusion', filters.conclusion);
  if (filters.assetId) parameters.set('asset_id', filters.assetId);
  if (filters.disposition) parameters.set('disposition', filters.disposition);
  parameters.set('page', String(filters.page ?? 1));
  parameters.set('page_size', String(filters.pageSize ?? 10));
  const suffix = parameters.size ? '?' + parameters.toString() : '';
  const response = await apiFetch(endpoint + suffix);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '漏洞数据加载失败');
  }
  return (await response.json()) as VulnerabilityListResponse;
}

export async function updateVulnerabilityDisposition(
  findingId: string, payload: VulnerabilityDispositionUpdate,
): Promise<VulnerabilityFinding> {
  const response = await apiFetch(endpoint + '/' + findingId + '/disposition', {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '漏洞处置保存失败');
  }
  return (await response.json()) as VulnerabilityFinding;
}
