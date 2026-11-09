import { apiFetch } from './client';
import type { BaselineTask } from '../types/baseline';

const endpoint = '/api/v1/baselines';
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    signal: options?.signal ?? AbortSignal.timeout(10_000),
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '请求失败，请稍后重试');
  }
  return response.json() as Promise<T>;
}

export const getBaselineTasks = () => request<BaselineTask[]>(endpoint);
export const createBaselineTask = (name: string, assetIds: string[]) => request<BaselineTask>(endpoint, { method: 'POST', body: JSON.stringify({ name, asset_ids: assetIds }) });
export const startBaselineTask = (id: string) => request<BaselineTask>(`${endpoint}/${id}/start`, { method: 'POST' });
export const cancelBaselineTask = (id: string) => request<BaselineTask>(`${endpoint}/${id}/cancel`, { method: 'POST' });
export const rerunBaselineTask = (id: string) => request<BaselineTask>(`${endpoint}/${id}/rerun`, { method: 'POST' });
