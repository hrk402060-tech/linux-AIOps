import { apiFetch } from './client';

export interface AuditLogEntry {
  id: string; source: string; action: string; operator: string; detail: string; target: string; occurred_at: string;
}
export interface AuditLogResponse { total: number; items: AuditLogEntry[]; }

export async function getAuditLog(params: URLSearchParams): Promise<AuditLogResponse> {
  const response = await apiFetch(`/api/v1/audit-log?${params}`, {
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error('审计日志加载失败');
  return response.json() as Promise<AuditLogResponse>;
}

export async function getAIProviderAudit(page = 1): Promise<AuditLogResponse> {
  return getAuditLog(new URLSearchParams({
    source: 'ai_provider_config', page: String(page), page_size: '8',
  }));
}
