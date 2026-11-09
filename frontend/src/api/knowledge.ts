import { apiFetch } from './client';
import type {
  KnowledgeAdvisory, KnowledgeBackfill, KnowledgeBackfillPayload, KnowledgeCoverage,
  KnowledgeImportPayload, KnowledgeRelease, KnowledgeSource, KnowledgeSummary, KnowledgeSyncRun,
} from '../types/knowledge';

const endpoint = '/api/v1/knowledge';

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

export const getKnowledgeSummary = () => request<KnowledgeSummary>(`${endpoint}/summary`);
export const getKnowledgeCoverage = () => request<KnowledgeCoverage>(`${endpoint}/coverage`);
export const getKnowledgeAdvisories = () => request<KnowledgeAdvisory[]>(`${endpoint}/advisories`);
export const getKnowledgeSources = () => request<KnowledgeSource[]>(`${endpoint}/sources`);
export const getKnowledgeReleases = () => request<KnowledgeRelease[]>(`${endpoint}/releases`);
export const getKnowledgeSyncRuns = () => request<KnowledgeSyncRun[]>(`${endpoint}/sync-runs`);

export function importKnowledgePackage(payload: KnowledgeImportPayload): Promise<KnowledgeRelease> {
  return request<KnowledgeRelease>(`${endpoint}/imports`, { method: 'POST', body: JSON.stringify(payload) });
}

export function rollbackKnowledgeRelease(id: string): Promise<KnowledgeRelease> {
  return request<KnowledgeRelease>(`${endpoint}/releases/${id}/rollback`, { method: 'POST' });
}

export function syncKnowledgeSource(id: string): Promise<KnowledgeSyncRun> {
  return request<KnowledgeSyncRun>(`${endpoint}/sources/${id}/sync`, { method: 'POST' });
}

export const getKnowledgeBackfills = () => request<KnowledgeBackfill[]>(`${endpoint}/backfills`);

export function createKnowledgeBackfill(payload: KnowledgeBackfillPayload): Promise<KnowledgeBackfill> {
  return request<KnowledgeBackfill>(`${endpoint}/backfills`, { method: 'POST', body: JSON.stringify(payload) });
}

export function resumeKnowledgeBackfill(id: string): Promise<KnowledgeBackfill> {
  return request<KnowledgeBackfill>(`${endpoint}/backfills/${id}/resume`, { method: 'POST' });
}

export function cancelKnowledgeBackfill(id: string): Promise<KnowledgeBackfill> {
  return request<KnowledgeBackfill>(`${endpoint}/backfills/${id}/cancel`, { method: 'POST' });
}
