import { apiFetch } from './client';
import type { CredentialRiskTask } from '../types/credential-risk';

const endpoint = '/api/v1/credential-risks';
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options, signal: options?.signal ?? AbortSignal.timeout(10_000),
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '请求失败，请稍后重试');
  }
  return response.json() as Promise<T>;
}
export const getCredentialRiskTasks = () => request<CredentialRiskTask[]>(endpoint);
export const createCredentialRiskTask = (name: string, credentialIds: string[]) => request<CredentialRiskTask>(endpoint, { method: 'POST', body: JSON.stringify({ name, credential_ids: credentialIds }) });
export const startCredentialRiskTask = (id: string) => request<CredentialRiskTask>(`${endpoint}/${id}/start`, { method: 'POST' });
export const cancelCredentialRiskTask = (id: string) => request<CredentialRiskTask>(`${endpoint}/${id}/cancel`, { method: 'POST' });
export const rerunCredentialRiskTask = (id: string) => request<CredentialRiskTask>(`${endpoint}/${id}/rerun`, { method: 'POST' });
