import { request } from './kubernetes';

export interface BackupRegistration {
  expected_revision: number;
  name: string;
  storage_key: string;
  namespaces: string[];
  include_persistent_volumes: boolean;
  sha256: string;
  byte_size: number;
}

export interface KubernetesBackup extends Omit<BackupRegistration, 'expected_revision'> {
  id: string;
  cluster_revision: number;
  status: 'registered' | 'verified' | 'invalid';
  verification_error: string | null;
  verified_at: string | null;
  restore_target: { target_key: string; reason: string } | null;
}

function endpoint(cluster: string, backup?: string) {
  const base = `/api/v1/kubernetes/clusters/${encodeURIComponent(cluster)}/backups`;
  return backup ? `${base}/${encodeURIComponent(backup)}` : base;
}

export function listBackups(cluster: string, offset: number, signal: AbortSignal) {
  return request<{ items: KubernetesBackup[]; total: number }>(
    `${endpoint(cluster)}?offset=${offset}&limit=10`, { signal }, '备份记录读取失败',
  );
}

export function registerBackup(cluster: string, body: BackupRegistration, signal: AbortSignal) {
  return request<KubernetesBackup>(endpoint(cluster), {
    method: 'POST', body: JSON.stringify(body), signal,
  }, '备份登记失败');
}

export function verifyBackup(cluster: string, backup: string, signal: AbortSignal) {
  return request<KubernetesBackup>(`${endpoint(cluster, backup)}/verify`, { method: 'POST', signal },
    '备份文件校验失败', 60_000);
}

export function saveRestoreTarget(
  cluster: string, backup: string,
  body: { expected_revision: number; target_key: string; reason: string }, signal: AbortSignal,
) {
  return request<KubernetesBackup>(`${endpoint(cluster, backup)}/restore-target`, {
    method: 'PUT', body: JSON.stringify(body), signal,
  }, '恢复候选目标登记失败');
}
