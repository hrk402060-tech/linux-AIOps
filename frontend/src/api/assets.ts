import { apiFetch } from './client';
import { apiErrorMessage } from '../utils/api-error-message';
import type { Asset, AssetCleanupPreview, AssetCreate, AssetLifecycleStatus, AssetUpdate } from '../types/asset';

const endpoint = '/api/v1/assets';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(apiErrorMessage(body?.detail, '请求失败，请稍后重试'));
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function getAssets(lifecycle = 'active'): Promise<Asset[]> {
  const url = lifecycle === 'active' ? endpoint : endpoint + '?lifecycle=' + encodeURIComponent(lifecycle);
  return request<Asset[]>(url);
}

export function createAsset(payload: AssetCreate): Promise<Asset> {
  return request<Asset>(endpoint, { method: 'POST', body: JSON.stringify(payload) });
}

export function updateAsset(id: string, payload: AssetUpdate): Promise<Asset> {
  return request<Asset>(`${endpoint}/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
}

export function deleteAsset(id: string): Promise<void> {
  return request<void>(`${endpoint}/${id}`, { method: 'DELETE' });
}

export function updateAssetLifecycle(
  id: string, status: AssetLifecycleStatus, reason: string,
): Promise<Asset> {
  return request<Asset>(`${endpoint}/${id}/lifecycle`, {
    method: 'PATCH', body: JSON.stringify({ status, reason }),
  });
}

export function getAssetCleanupPreview(id: string): Promise<AssetCleanupPreview> {
  return request<AssetCleanupPreview>(`${endpoint}/${id}/cleanup-preview`);
}

export function cleanupAsset(
  id: string, reason: string, confirmation: string,
): Promise<{ status: string; audit_id: string }> {
  return request(`${endpoint}/${id}/cleanup`, {
    method: 'POST', body: JSON.stringify({ reason, confirmation }),
  });
}

export function checkAsset(id: string): Promise<Asset> {
  return request<Asset>(`${endpoint}/${id}/check`, { method: 'POST' });
}

export function probeSshHostKey(id: string): Promise<import('../types/asset').SshHostKeyProbe> {
  return request(`${endpoint}/${id}/ssh-host-key/probe`, { method: 'POST' });
}

export function getSshHostKey(id: string): Promise<import('../types/asset').SshHostKeyRegistration> {
  return request(`${endpoint}/${id}/ssh-host-key`);
}

export function confirmSshHostKey(id: string, fingerprint: string): Promise<import('../types/asset').SshHostKeyRegistration> {
  return request(`${endpoint}/${id}/ssh-host-key/confirm`, {
    method: 'POST', body: JSON.stringify({ fingerprint }),
  });
}
