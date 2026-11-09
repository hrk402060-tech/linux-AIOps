import type { Credential, CredentialCreate, CredentialUpdate } from '../types/credential';
import { apiFetch } from './client';

const endpoint = '/api/v1/credentials';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '请求失败，请稍后重试');
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function getCredentials(): Promise<Credential[]> {
  return request<Credential[]>(endpoint);
}

export function createCredential(payload: CredentialCreate): Promise<Credential> {
  return request<Credential>(endpoint, { method: 'POST', body: JSON.stringify(payload) });
}

export function updateCredential(id: string, payload: CredentialUpdate): Promise<Credential> {
  return request<Credential>(`${endpoint}/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
}

export function deleteCredential(id: string): Promise<void> {
  return request<void>(`${endpoint}/${id}`, { method: 'DELETE' });
}
