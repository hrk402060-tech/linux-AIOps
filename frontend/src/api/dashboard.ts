import { apiFetch, parseApiResponse } from './client';
import type { DashboardSnapshot } from '../types/dashboard';

export async function getDashboard(signal?: AbortSignal): Promise<DashboardSnapshot> {
  const response = await apiFetch('/api/v1/dashboard', {
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(10_000)]) : AbortSignal.timeout(10_000),
  });
  signal?.throwIfAborted();
  return parseApiResponse<DashboardSnapshot>(response, '总览数据加载失败');
}
