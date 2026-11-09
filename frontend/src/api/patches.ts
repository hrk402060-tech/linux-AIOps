import { apiFetch } from './client';
import { apiErrorMessage } from '../utils/api-error-message';

export interface PatchAdvice {
  id: string; finding_id: string; asset_id: string; asset_name: string; cve_id: string;
  package_name: string; current_version: string; target_version: string; severity: string;
  source_name: string; vendor_url: string | null; evidence_chain: Array<Record<string, string>>;
  status: 'pending_approval' | 'pending_verification' | 'fixed' | 'ignored' | 'risk_accepted' | 'invalid_target' | 'manual_confirmation'; execution_enabled: false;
  target_version_relation?: 'lower_than_current' | 'equal_to_current' | 'higher_than_current' | 'unknown';
  target_version_issue?: string | null;
  rollback_plan: string;
  approval_phrase: string;
  history_count: number; latest_scan_id: string; latest_scan_name: string; latest_detected_at: string;
}

export async function getPatchAdvice(): Promise<PatchAdvice[]> {
  const response = await apiFetch('/api/v1/patches');
  if (!response.ok) throw new Error('补丁建议加载失败');
  return response.json() as Promise<PatchAdvice[]>;
}

export async function approvePatchAdvice(id: string, reason: string, confirmation: string): Promise<PatchAdvice> {
  const response = await apiFetch(`/api/v1/patches/${id}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, confirmation }), signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(apiErrorMessage(body?.detail, '补丁建议审批失败'));
  }
  return response.json() as Promise<PatchAdvice>;
}
