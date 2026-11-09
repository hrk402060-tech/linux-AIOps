import { apiFetch } from './client';

export type DiagnosticGrantStatus = 'active' | 'expired' | 'disabled' | 'asset_disabled' | 'target_changed' | 'host_key_changed';
export interface DiagnosticAction {
  id: string; label: string; domain: string; available: boolean; read_only: boolean;
  required_permission: string; status: string;
}
export interface DiagnosticGrant {
  kind?: 'linux' | 'kubernetes' | 'docker' | 'mysql' | 'redis' | 'rabbitmq';
  asset_id: string; asset_name: string; host: string; port: number; username: string;
  action_ids: string[]; expires_at: string; enabled: boolean; effective_status: DiagnosticGrantStatus;
  revision: number; key_fingerprint: string; host_fingerprint: string; secret_configured: boolean;
  operator: string; updated_at: string;
  kubernetes_config?: {
    endpoint: string; service_account_namespace: string; service_account_name: string; namespaces: string[];
  };
  docker_config?: { endpoint: string; proxy_identity: string };
  mysql_config?: { host: string; port: number; username: string; database: string };
  redis_config?: { host: string; port: number; username: string; database: number };
  rabbitmq_config?: { host: string; port: number; username: string; vhost: string };
  token_masked?: string;
  ca_fingerprint?: string;
}
export interface DiagnosticGrantList { total: number; items: DiagnosticGrant[]; catalog: DiagnosticAction[] }
export interface LinuxDiagnosticGrantInput {
  kind?: 'linux';
  asset_id: string; username: string; private_key?: string; action_ids: string[]; expires_at: string;
  expected_revision: number; confirmation: string; reason: string; gateway_confirmed: true;
}
export interface KubernetesDiagnosticGrantInput {
  kind: 'kubernetes'; asset_id: string; expected_revision: number; confirmation: string; reason: string;
  action_ids: string[]; expires_at: string; endpoint: string; service_account_namespace: string;
  service_account_name: string; namespaces: string[]; token?: string; ca_pem?: string; rbac_confirmed: true;
}
export interface DockerDiagnosticGrantInput {
  kind: 'docker'; asset_id: string; expected_revision: number; confirmation: string; reason: string;
  endpoint: string; proxy_identity: string; token?: string; ca_pem?: string; action_ids: string[];
  expires_at: string; proxy_confirmed: true;
}
export interface MySQLDiagnosticGrantInput {
  kind: 'mysql'; asset_id: string; expected_revision: number; confirmation: string; reason: string;
  port: number; username: string; password?: string; ca_pem?: string; database: string;
  action_ids: string[]; expires_at: string; mysql_confirmed: true;
}
export interface RedisDiagnosticGrantInput {
  kind: 'redis'; asset_id: string; expected_revision: number; confirmation: string; reason: string;
  port: number; username: string; password?: string; ca_pem?: string; database: number;
  action_ids: string[]; expires_at: string; redis_confirmed: true;
}
export interface RabbitMQDiagnosticGrantInput {
  kind: 'rabbitmq'; asset_id: string; expected_revision: number; confirmation: string; reason: string;
  port: number; username: string; password?: string; ca_pem?: string; vhost: string;
  action_ids: string[]; expires_at: string; rabbitmq_confirmed: true;
}
export type DiagnosticGrantInput = LinuxDiagnosticGrantInput | KubernetesDiagnosticGrantInput | DockerDiagnosticGrantInput | MySQLDiagnosticGrantInput | RedisDiagnosticGrantInput | RabbitMQDiagnosticGrantInput;
export interface DiagnosticGrantAudit {
  id: string; asset_id: string; action: string; operator: string; reason: string;
  revision: number; created_at: string;
}
export class DiagnosticApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = 'DiagnosticApiError'; }
}
const endpoint = '/api/v1/diagnostic-grants';
async function request<T>(url: string, options?: RequestInit, safeError?: string): Promise<T> {
  const response = await apiFetch(url, {
    ...options, headers: { 'Content-Type': 'application/json', ...options?.headers },
    signal: options?.signal ?? AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new DiagnosticApiError(safeError ?? (typeof body?.detail === 'string' ? body.detail : '只读诊断授权请求失败'), response.status);
  }
  return await response.json() as T;
}
export function getDiagnosticGrants(offset = 0, assetId?: string): Promise<DiagnosticGrantList> {
  const query = new URLSearchParams({ offset: String(offset), limit: '50' });
  if (assetId) query.set('asset_id', assetId);
  return request(`${endpoint}?${query}`);
}
export async function saveDiagnosticGrant(payload: DiagnosticGrantInput): Promise<DiagnosticGrant> {
  const safeError = payload.kind === 'kubernetes' ? 'Kubernetes 授权保存失败，请检查权限、版本、范围与凭据后重试'
    : payload.kind === 'docker' ? 'Docker 代理授权保存失败，请检查地址、证书、身份与凭据后重试'
    : payload.kind === 'mysql' ? 'MySQL 授权保存失败，请检查地址、TLS、账号与凭据后重试'
    : payload.kind === 'redis' ? 'Redis 授权保存失败，请检查地址、TLS、账号与凭据后重试'
    : payload.kind === 'rabbitmq' ? 'RabbitMQ 授权保存失败，请检查地址、TLS、账号、vhost 与凭据后重试' : undefined;
  try {
    return await request(endpoint, { method: 'POST', body: JSON.stringify(payload) }, safeError);
  } catch (error) {
    if (!safeError || error instanceof DiagnosticApiError) throw error;
    throw new Error(safeError);
  }
}
export function revokeDiagnosticGrant(assetId: string, payload: {
  expected_revision: number; confirmation: string; reason: string;
}): Promise<DiagnosticGrant> {
  return request(`${endpoint}/${encodeURIComponent(assetId)}/revoke`, { method: 'POST', body: JSON.stringify(payload) });
}
export function getDiagnosticGrantAudit(assetId: string): Promise<{ items: DiagnosticGrantAudit[] }> {
  return request(`${endpoint}/${encodeURIComponent(assetId)}/audit`);
}
export interface DiagnosticGrantTestResult {
  asset_id: string; kind: 'linux' | 'kubernetes' | 'docker' | 'mysql' | 'redis' | 'rabbitmq'; status: 'completed' | 'partial' | 'failed';
  summary: string; error_code?: string | null; read_only: true; grant_revision: number;
  evidence_keys: string[];
}
export function testDiagnosticGrant(assetId: string, payload: {
  expected_revision: number; confirmation: string; reason: string;
}): Promise<DiagnosticGrantTestResult> {
  return request(`${endpoint}/${encodeURIComponent(assetId)}/test`, {
    method: 'POST', body: JSON.stringify(payload), signal: AbortSignal.timeout(40_000),
  });
}
