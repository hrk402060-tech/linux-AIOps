import { apiFetch } from './client';
import type { ReportCreate, ScanReport } from '../types/report';

const endpoint = '/api/v1/reports';

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
  return (await response.json()) as T;
}

export const getReports = () => request<ScanReport[]>(endpoint);

export function createReport(payload: ReportCreate): Promise<ScanReport> {
  return request<ScanReport>(endpoint, { method: 'POST', body: JSON.stringify(payload) });
}

export function retryReport(id: string): Promise<ScanReport> {
  return request<ScanReport>(endpoint + '/' + id + '/retry', {
    method: 'POST', body: JSON.stringify({}),
  });
}

export async function downloadReport(item: ScanReport): Promise<void> {
  const response = await apiFetch(endpoint + '/' + item.id + '/download');
  if (!response.ok) throw new Error('报告下载失败');
  const blobUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = item.file_name ?? 'scan-report.' + item.format;
  anchor.click();
  URL.revokeObjectURL(blobUrl);
}
