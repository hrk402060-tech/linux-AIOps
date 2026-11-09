import { apiFetch, parseApiResponse } from './client';

export type RecoveryStatus = 'observing' | 'verified' | 'failed' | 'blocked' | 'cancelled';
export type RecoveryMetric = 'availability' | 'cpu_percent' | 'memory_percent' | 'swap_percent' | 'disk_percent' | 'inode_percent' | 'load_1m';
export interface RecoveryEvaluation {
  id: string; verification_id: string; outcome: RecoveryStatus; reason_code: string; summary: string;
  operator: string; trace_id: string; evaluated_at: string; evidence: Record<string, unknown>[];
}
export interface RecoveryVerification {
  id: string; alert_id: string; asset_id: string; kind: 'monitor' | 'probe'; metric: RecoveryMetric | null;
  threshold: number | null; probe_id: string | null; observation_minutes: number; max_gap_seconds: number;
  status: RecoveryStatus; version: number; operator: string; reason: string; started_at: string;
  deadline_at: string; finished_at: string | null; last_evaluation: RecoveryEvaluation | null;
  is_current?: boolean;
}
export interface RecoveryOptions {
  active_verification?: boolean;
  asset_enabled: boolean; metrics: { value: RecoveryMetric; label: string }[];
  probes: { id: string; name: string }[]; collection_mode: 'existing_evidence_only';
}
export interface RecoveryHistory { total: number; items: RecoveryVerification[]; options: RecoveryOptions }
export interface RecoveryCreate {
  kind: 'monitor' | 'probe'; metric?: RecoveryMetric; threshold?: number; probe_id?: string;
  observation_minutes: number; max_gap_seconds: number; confirmation: string; reason: string;
}
export interface RecoveryAction { expected_version: number; confirmation: string; reason: string }
export interface RecoveryEvaluationHistory { total: number; items: RecoveryEvaluation[] }
export class RecoveryRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
async function request<T>(url: string, payload?: object): Promise<T> {
  const response = await apiFetch(url, payload ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  } : undefined);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new RecoveryRequestError(typeof body?.detail === 'string' ? body.detail : '恢复验证请求失败', response.status);
  }
  return parseApiResponse<T>(response, '恢复验证请求失败');
}
const endpoint = (alertId: string) => `/api/v1/alerts/${encodeURIComponent(alertId)}/recovery-verifications`;
export const getRecoveryHistory = (alertId: string, offset = 0) => request<RecoveryHistory>(`${endpoint(alertId)}?offset=${offset}&limit=10`);
export const createRecovery = (alertId: string, payload: RecoveryCreate) => request<RecoveryVerification>(endpoint(alertId), payload);
export const actOnRecovery = (alertId: string, item: RecoveryVerification, action: 'evaluate' | 'cancel', payload: RecoveryAction) =>
  request<RecoveryVerification>(`${endpoint(alertId)}/${encodeURIComponent(item.id)}/${action}`, payload);
export const getRecoveryEvaluations = (alertId: string, id: string, offset = 0) =>
  request<RecoveryEvaluationHistory>(`${endpoint(alertId)}/${encodeURIComponent(id)}/evaluations?offset=${offset}&limit=10`);
