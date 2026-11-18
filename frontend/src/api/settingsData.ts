import { apiFetch, parseApiResponse } from './client';

export interface CleanupPreview {
  retention_days: number; cutoff_at: string;
  counts: Array<{ resource: string; label: string; count: number }>; total: number;
}
export interface SettingsDataJob {
  id: string; kind: 'cleanup' | 'backup' | 'verify_backup';
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: string; started_at: string | null; finished_at: string | null; error_code: string | null;
  result: {
    verified?: boolean; verification_target?: string; filename?: string; table_count?: number;
    row_count?: number; bytes?: number; sha256?: string; key_version?: string;
    requires_original_master_key?: boolean; total?: number;
    counts?: Array<{ resource: string; label: string; count: number }>;
  };
}
const base = '/api/v1/system/data-jobs';
export async function getCleanupPreview(): Promise<CleanupPreview> {
  return parseApiResponse<CleanupPreview>(await apiFetch(`${base}/preview`), '清理范围统计失败');
}
export async function getSettingsDataJobs(): Promise<{ items: SettingsDataJob[] }> {
  return parseApiResponse<{ items: SettingsDataJob[] }>(await apiFetch(base), '数据任务加载失败');
}
export async function createSettingsDataJob(payload: {
  kind: SettingsDataJob['kind']; confirmation: string; backup_job_id?: string;
}): Promise<SettingsDataJob> {
  return parseApiResponse<SettingsDataJob>(await apiFetch(base, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }), '数据任务提交失败');
}
