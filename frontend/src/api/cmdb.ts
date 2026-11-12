import { apiFetch, parseApiResponse } from './client';
import type {
  CmdbEntity, CmdbEntityPayload, CmdbEntityType, CmdbLifecycleStatus,
  CmdbRelationStatus, CmdbServiceAsset, CmdbTreeResponse,
  CmdbDependencyStatus, CmdbServiceDependency,
} from '../types/cmdb';

const endpoint = '/api/v1/cmdb';
const collections: Record<CmdbEntityType, string> = {
  business: 'businesses', application: 'applications', service: 'services', api: 'apis',
};

export interface CmdbDiscoveryService {
  name: string;
  source: 'systemd' | 'container';
  state: string;
}

export interface CmdbDiscoveryAsset {
  asset_id: string;
  asset_name: string;
  host: string;
  observed_at: string;
  status: string;
  services: CmdbDiscoveryService[];
}

export interface CmdbDiscoveryResponse {
  generated_at: string;
  source: 'latest_readonly_snapshot';
  assets: CmdbDiscoveryAsset[];
}

function request<T>(url: string, options?: RequestInit, fallback = '业务服务请求失败'): Promise<T> {
  return apiFetch(url, {
    ...options,
    headers: options?.body ? { 'Content-Type': 'application/json', ...options.headers } : options?.headers,
  }).then((response) => parseApiResponse<T>(response, fallback));
}

export function getCmdbTree(lifecycle: CmdbLifecycleStatus | 'all' = 'active', query = ''): Promise<CmdbTreeResponse> {
  const params = new URLSearchParams({ lifecycle });
  if (query.trim()) params.set('query', query.trim());
  return request(`${endpoint}/tree?${params.toString()}`, undefined, '服务目录加载失败');
}

export function getCmdbDiscoveryPreview(): Promise<CmdbDiscoveryResponse> {
  return request(`${endpoint}/discovery/preview`, undefined, '自动发现预览加载失败');
}

export interface CmdbDiscoveryConfirmResponse {
  service_id: string;
  service_code: string;
  service_name: string;
  relation_id: string;
  relation_status: CmdbRelationStatus;
  created_service: boolean;
  restored_relation: boolean;
}

export function confirmCmdbDiscovery(payload: {
  application_id: string;
  asset_id: string;
  service_name: string;
  source: 'systemd' | 'container';
}): Promise<CmdbDiscoveryConfirmResponse> {
  return request(`${endpoint}/discovery/confirm`, {
    method: 'POST', body: JSON.stringify(payload),
  }, '自动发现确认失败');
}

export function createCmdbEntity(type: CmdbEntityType, payload: CmdbEntityPayload): Promise<CmdbEntity> {
  return request(`${endpoint}/${collections[type]}`, {
    method: 'POST', body: JSON.stringify(payload),
  }, '配置项创建失败');
}

export function updateCmdbEntity(
  type: CmdbEntityType, id: string,
  payload: Partial<CmdbEntityPayload> & { lifecycle_status?: CmdbLifecycleStatus; lifecycle_reason?: string },
): Promise<CmdbEntity> {
  return request(`${endpoint}/${collections[type]}/${id}`, {
    method: 'PATCH', body: JSON.stringify(payload),
  }, '配置项更新失败');
}

export function bindServiceAsset(serviceId: string, assetId: string, relationRole: string): Promise<CmdbServiceAsset> {
  return request(`${endpoint}/services/${serviceId}/assets`, {
    method: 'POST', body: JSON.stringify({ asset_id: assetId, relation_role: relationRole }),
  }, '服务器绑定失败');
}

export function updateServiceAsset(
  relationId: string, status: CmdbRelationStatus, relationRole?: string,
): Promise<CmdbServiceAsset> {
  return request(`${endpoint}/service-assets/${relationId}`, {
    method: 'PATCH', body: JSON.stringify({ status, ...(relationRole ? { relation_role: relationRole } : {}) }),
  }, '服务器关系更新失败');
}

export function getServiceDependencies(serviceId: string): Promise<CmdbServiceDependency[]> {
  return request(`${endpoint}/services/${serviceId}/dependencies`, undefined, '服务依赖加载失败');
}

export function createServiceDependency(
  serviceId: string,
  payload: { target_service_id: string; dependency_type: string; description?: string },
): Promise<CmdbServiceDependency> {
  return request(`${endpoint}/services/${serviceId}/dependencies`, {
    method: 'POST', body: JSON.stringify(payload),
  }, '服务依赖创建失败');
}

export function updateServiceDependency(
  dependencyId: string,
  payload: { status?: CmdbDependencyStatus; description?: string | null },
): Promise<CmdbServiceDependency> {
  return request(`${endpoint}/service-dependencies/${dependencyId}`, {
    method: 'PATCH', body: JSON.stringify(payload),
  }, '服务依赖更新失败');
}
