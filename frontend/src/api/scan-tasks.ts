import { apiFetch } from './client';
import { apiErrorMessage } from '../utils/api-error-message';
import type { ScanSchedule, ScanScheduleCreate, ScanTask, ScanTaskCreate } from '../types/scan-task';

const endpoint = '/api/v1/scan-tasks';

export class ScanTaskRequestError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message); this.name = 'ScanTaskRequestError';
  }
}

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
    throw new ScanTaskRequestError(apiErrorMessage(body?.detail, '请求失败，请稍后重试'), response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function getScanTasks(archiveView = 'active'): Promise<ScanTask[]> {
  const url = archiveView === 'active' ? endpoint : endpoint + '?archive_view=' + encodeURIComponent(archiveView);
  return request<ScanTask[]>(url);
}

export function createScanTask(payload: ScanTaskCreate): Promise<ScanTask> {
  return request<ScanTask>(endpoint, { method: 'POST', body: JSON.stringify(payload) });
}

export function startScanTask(id: string): Promise<ScanTask> {
  return request<ScanTask>(endpoint + '/' + id + '/start', { method: 'POST' });
}

export function cancelScanTask(id: string): Promise<ScanTask> {
  return request<ScanTask>(endpoint + '/' + id + '/cancel', { method: 'POST' });
}

export function rerunScanTask(id: string): Promise<ScanTask> {
  return request<ScanTask>(endpoint + '/' + id + '/rerun', { method: 'POST' });
}

export function archiveScanTask(id: string, archived: boolean): Promise<ScanTask> {
  return request<ScanTask>(endpoint + '/' + id + '/archive', {
    method: 'PATCH', body: JSON.stringify({ archived }),
  });
}

export function removeScanTask(id: string, reason: string): Promise<void> {
  return request<void>(endpoint + '/' + id, {
    method: 'DELETE', body: JSON.stringify({ confirmation: 'DELETE ' + id, reason: reason.trim() }),
  });
}

export async function downloadScanReport(id: string): Promise<void> {
  const response = await apiFetch(endpoint + '/' + id + '/report');
  if (!response.ok) throw new Error('报告下载失败');
  const blobUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = 'scan-' + id + '.txt';
  anchor.click();
  URL.revokeObjectURL(blobUrl);
}

const schedulesEndpoint = '/api/v1/scan-schedules';
export const getScanSchedules = () => request<ScanSchedule[]>(schedulesEndpoint);
export const createScanSchedule = (payload: ScanScheduleCreate) => request<ScanSchedule>(
  schedulesEndpoint, { method: 'POST', body: JSON.stringify(payload) },
);
export const setScanScheduleEnabled = (id: string, enabled: boolean) => request<ScanSchedule>(
  `${schedulesEndpoint}/${id}/enabled`, { method: 'PATCH', body: JSON.stringify({ enabled }) },
);
export const getScheduledTasks = (id: string) => request<ScanTask[]>(
  `${schedulesEndpoint}/${id}/tasks`,
);
