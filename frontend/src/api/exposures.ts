import { apiFetch } from './client';
import type { ExposureTask } from '../types/exposure';

const endpoint = '/api/v1/exposures';
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

export const getExposureTasks = () => request<ExposureTask[]>(endpoint);
export const createExposureTask = (name: string, assetIds: string[]) => request<ExposureTask>(endpoint, { method: 'POST', body: JSON.stringify({ name, asset_ids: assetIds }) });
export const startExposureTask = (id: string) => request<ExposureTask>(`${endpoint}/${id}/start`, { method: 'POST' });
export const cancelExposureTask = (id: string) => request<ExposureTask>(`${endpoint}/${id}/cancel`, { method: 'POST' });
export const rerunExposureTask = (id: string) => request<ExposureTask>(`${endpoint}/${id}/rerun`, { method: 'POST' });
