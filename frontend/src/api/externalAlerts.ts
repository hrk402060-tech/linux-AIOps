import { apiFetch, parseApiResponse } from './client';
import type { NotificationSummary } from './alertNotifications';
import type { RecoveryVerification } from './recovery';

export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';
export type AlertStatus = 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'closed';
export type AnalysisStatus = 'running' | 'completed' | 'partial' | 'failed' | 'cancelled';
export type DiagnosticJobStatus = 'queued' | 'running' | 'retry_wait' | 'completed' | 'partial' | 'failed' | 'cancelled';

export interface DiagnosticJob {
  id: string;
  alert_id: string;
  asset_id: string;
  grant_revision: number | null;
  status: DiagnosticJobStatus;
  display_status: DiagnosticJobStatus | 'cancelling';
  trigger_type: string;
  operator: string;
  reason: string;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string | null;
  cancellation_requested_at: string | null;
  last_error_code: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface DiagnosticJobHistory {
  total: number;
  items: DiagnosticJob[];
}

export class ExternalAlertRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export interface DiagnosticJobRequest {
  confirmation: string;
  reason: string;
}
export type ConnectorStatus = 'active' | 'disabled';

export interface AlertAnalysis {
  id: string;
  job_id?: string | null;
  attempt_number?: number | null;
  alert_id: string;
  asset_id: string;
  status: AnalysisStatus;
  trigger_type: string;
  policy_version: string;
  summary: string;
  evidence: Record<string, unknown>;
  error_code: string | null;
  operator: string;
  trace_id: string;
  started_at: string;
  finished_at: string | null;
}

export interface AlertTimelineEntry {
  id: number;
  alert_id: string;
  event_type: string;
  previous_status: string | null;
  status: string;
  note: string;
  operator: string;
  trace_id: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ExternalAlert {
  id: string;
  connector_id: string;
  connector_name?: string;
  external_id: string;
  title: string;
  description: string;
  severity: AlertSeverity;
  status: AlertStatus;
  asset_id: string;
  asset_name: string;
  payload: Record<string, unknown>;
  incident_id: string | null;
  source_type?: string | null;
  source_key?: string | null;
  occurrence_count?: number;
  received_at: string;
  updated_at: string;
  latest_analysis: AlertAnalysis | null;
  latest_job?: DiagnosticJob | null;
  latest_recovery?: RecoveryVerification | null;
  notification_summary?: NotificationSummary;
}

export interface ExternalAlertList {
  total: number;
  items: ExternalAlert[];
  summary?: {
    open: number;
    urgent: number;
    pending_rca: number;
    linked: number;
  };
}

export interface AlertConnector {
  id: string;
  name: string;
  source_type: string;
  status: ConnectorStatus;
  secret_masked: string;
  operator: string;
  created_at: string;
  updated_at: string;
  rotated_at: string | null;
}

export interface ConnectorRuntime {
  connector_id: string;
  health: 'disabled' | 'unverified' | 'healthy' | 'error';
  success_count: number;
  failure_count: number;
  last_received_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error_code: string | null;
  last_error: string | null;
  observed_since: string | null;
}

export type ConnectorReceiptResult = 'success' | 'failure';
export interface ConnectorReceipt {
  id: string;
  connector_id: string;
  received_at: string;
  http_status: number;
  result: ConnectorReceiptResult;
  outcome: 'created' | 'duplicate' | 'reopened' | 'rejected';
  error_code: string | null;
  message: string;
  alert_id: string | null;
}
export interface ConnectorReceiptPage {
  items: ConnectorReceipt[];
  total: number;
  offset: number;
  limit: number;
}

export interface ConnectorSecretResult extends AlertConnector {
  secret: string;
}

export interface ConnectorTestRequest {
  method: 'POST';
  path: string;
  signature_algorithm: 'HMAC-SHA256';
  replay_window_seconds: number;
  headers: Record<string, string>;
  body: string;
  warning: string;
}

export type ConnectorPreflightStatus = 'passed' | 'blocked' | 'unverified';
export type ConnectorPreflightCheckKey = 'payload' | 'connector' | 'asset' | 'authorization' | 'notifications' | 'runtime';

export interface ConnectorPreflightCheck {
  key: ConnectorPreflightCheckKey;
  status: ConnectorPreflightStatus;
  message: string;
}

export interface ConnectorPreflightResult {
  read_only: boolean;
  configuration_ready: boolean;
  runtime_verified: boolean;
  checks: ConnectorPreflightCheck[];
  asset: { id: string; name: string; host: string } | null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    signal: options?.signal ?? AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ExternalAlertRequestError(body?.detail ?? '外部告警请求失败，请检查权限或稍后重试', response.status);
  }
  return parseApiResponse<T>(response, '外部告警请求失败，请检查权限或稍后重试');
}

export function getExternalAlerts(filters: Record<string, string>): Promise<ExternalAlertList> {
  const query = new URLSearchParams(filters).toString();
  return request(`/api/v1/alerts${query ? `?${query}` : ''}`);
}

export function getExternalAlert(id: string): Promise<ExternalAlert> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}`);
}

export function getAlertTimeline(id: string, signal?: AbortSignal): Promise<AlertTimelineEntry[]> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/timeline`, signal ? { signal } : undefined);
}

export function getAlertAnalyses(id: string, offset = 0, limit = 20, jobId?: string): Promise<AlertAnalysis[]> {
  const jobFilter = jobId ? `job_id=${encodeURIComponent(jobId)}&` : '';
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/analyses?${jobFilter}offset=${offset}&limit=${limit}`);
}

export function updateExternalAlertStatus(
  id: string, status: AlertStatus, note: string,
): Promise<ExternalAlert> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/status`, {
    method: 'PATCH', body: JSON.stringify({ status, note }),
  });
}

export function analyzeExternalAlert(id: string, payload: DiagnosticJobRequest): Promise<DiagnosticJob> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/diagnostics`, {
    method: 'POST', body: JSON.stringify(payload),
  });
}

export function getDiagnosticJobs(id: string, offset = 0, limit = 10): Promise<DiagnosticJobHistory> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/diagnostic-jobs?offset=${offset}&limit=${limit}`);
}

export function cancelDiagnosticJob(alertId: string, id: string, payload: DiagnosticJobRequest): Promise<DiagnosticJob> {
  return request(`/api/v1/alerts/${encodeURIComponent(alertId)}/diagnostic-jobs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST', body: JSON.stringify(payload),
  });
}

export function createIncidentFromAlert(id: string, description: string): Promise<unknown> {
  return request(`/api/v1/alerts/${encodeURIComponent(id)}/incident`, {
    method: 'POST', body: JSON.stringify({ description }),
  });
}

export function getAlertConnectors(): Promise<AlertConnector[]> {
  return request('/api/v1/alert-connectors');
}

export function getAlertConnectorRuntime(): Promise<ConnectorRuntime[]> {
  return request('/api/v1/alert-connectors/runtime');
}

export function getAlertConnectorReceipts(
  id: string, offset = 0, limit = 20, result?: ConnectorReceiptResult,
): Promise<ConnectorReceiptPage> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (result) query.set('result', result);
  return request(`/api/v1/alert-connectors/${encodeURIComponent(id)}/receipts?${query}`);
}

export function createAlertConnector(payload: {
  name: string; source_type: string;
}): Promise<ConnectorSecretResult> {
  return request('/api/v1/alert-connectors', {
    method: 'POST', body: JSON.stringify(payload),
  });
}

export function updateAlertConnector(id: string, status: ConnectorStatus): Promise<AlertConnector> {
  return request(`/api/v1/alert-connectors/${encodeURIComponent(id)}/status`, {
    method: 'PATCH', body: JSON.stringify({ status }),
  });
}

export function rotateAlertConnector(id: string, confirmation: string): Promise<ConnectorSecretResult> {
  return request(`/api/v1/alert-connectors/${encodeURIComponent(id)}/rotate`, {
    method: 'POST', body: JSON.stringify({ confirmation: confirmation.trim() }),
  });
}

export function createAlertConnectorTestRequest(id: string): Promise<ConnectorTestRequest> {
  return request(`/api/v1/alert-connectors/${encodeURIComponent(id)}/test-request`, {
    method: 'POST',
  });
}

export function preflightAlertConnector(id: string, samplePayload: string): Promise<ConnectorPreflightResult> {
  return request(`/api/v1/alert-connectors/${encodeURIComponent(id)}/preflight`, {
    method: 'POST', body: JSON.stringify({ sample_payload: samplePayload }),
  });
}
