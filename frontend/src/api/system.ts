import { apiFetch, parseApiResponse } from './client';

export interface SystemStatus {
  database_status: string;
  database_engine: string;
  schema_revision: string | null;
  monitoring_interval_seconds: number;
  knowledge_sync_interval_seconds: number;
  terminal: { max_connections: number; idle_timeout_seconds: number; max_duration_seconds: number; host_key_verification: boolean };
  security: { scan_read_only_default: boolean; ai_direct_execution: boolean; credential_encryption: string; authentication: string };
  diagnostic_scheduler: {
    enabled: boolean; started_at: string | null; last_loop_at: string | null; last_claim_at: string | null;
    status: 'running' | 'stale' | 'error' | 'disabled'; stale_after_seconds: number;
    recovered_jobs: number; last_error: string | null; last_error_at: string | null;
    queued: number; running: number; retry_wait: number; active_slots: number;
  };
  health?: {
    status: 'healthy' | 'degraded' | 'unavailable' | 'unknown';
    is_stale: boolean;
    stale_after_seconds: number;
    last_observed_at: string | null;
  };
  checked_at: string;
}

export interface AuthPolicy {
  login_failure_threshold: number;
  lock_duration_seconds: number;
  session_ttl_seconds: number;
  require_mfa: boolean;
  mfa_enforcement: 'off' | 'admins' | 'all';
  allow_user_mfa_reset: boolean;
  source: string;
  updated_by: string;
  updated_at: string | null;
}

export async function getSystemStatus(): Promise<SystemStatus> {
  const response = await apiFetch('/api/v1/system/status');
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '系统状态加载失败');
  }
  return (await response.json()) as SystemStatus;
}

export async function getAuthPolicy(): Promise<AuthPolicy> {
  const response = await apiFetch('/api/v1/system/auth-policy');
  return readAuthPolicy(response, '登录安全策略加载失败');
}

export async function saveAuthPolicy(
  payload: Pick<AuthPolicy, 'login_failure_threshold' | 'lock_duration_seconds' | 'session_ttl_seconds' | 'mfa_enforcement' | 'allow_user_mfa_reset'>,
): Promise<AuthPolicy> {
  const response = await apiFetch('/api/v1/system/auth-policy', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      login_failure_threshold: payload.login_failure_threshold,
      lock_duration_seconds: payload.lock_duration_seconds,
      session_ttl_seconds: payload.session_ttl_seconds,
      require_mfa: payload.mfa_enforcement !== 'off',
      mfa_enforcement: payload.mfa_enforcement,
      allow_user_mfa_reset: payload.allow_user_mfa_reset,
    }),
  });
  return readAuthPolicy(response, '登录安全策略保存失败');
}

async function readAuthPolicy(response: Response, fallback: string): Promise<AuthPolicy> {
  const value = await parseApiResponse<unknown>(response, fallback);
  if (!value || typeof value !== 'object') throw new Error('登录安全策略响应无效，请重新加载');
  const policy = value as Record<string, unknown>;
  const integerInRange = (field: string, min: number, max: number) => {
    const candidate = policy[field];
    return typeof candidate === 'number' && Number.isInteger(candidate)
      && candidate >= min && candidate <= max;
  };
  if (!integerInRange('login_failure_threshold', 3, 20)
    || !integerInRange('lock_duration_seconds', 60, 86400)
    || !integerInRange('session_ttl_seconds', 900, 604800)
    || typeof policy.require_mfa !== 'boolean' || typeof policy.source !== 'string'
    || !['off', 'admins', 'all'].includes(policy.mfa_enforcement as string)
    || typeof policy.allow_user_mfa_reset !== 'boolean'
    || typeof policy.updated_by !== 'string'
    || !(policy.updated_at === null || (
      typeof policy.updated_at === 'string' && !Number.isNaN(Date.parse(policy.updated_at))
    ))) {
    throw new Error('登录安全策略响应无效，请重新加载');
  }
  return value as AuthPolicy;
}
