import { apiFetch } from './client';
import type { DashboardSnapshot } from '../types/dashboard';

export async function getDashboard(): Promise<DashboardSnapshot> {
  const response = await apiFetch('/api/v1/dashboard');
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '总览数据加载失败');
  }
  return (await response.json()) as DashboardSnapshot;
}
