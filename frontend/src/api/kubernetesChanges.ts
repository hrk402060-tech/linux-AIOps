import { request } from './kubernetes';

export type ChangeParameters =
  | { action: 'scale'; additional_nodes: number }
  | { action: 'upgrade'; target_version: string }
  | { action: 'backup'; namespace: string }
  | { action: 'restore'; namespace: string; backup_name: string }
  | { action: 'helm'; namespace: string; release_name: string; chart_digest: string };

export interface ChangePreflight {
  trace_id: string;
  cluster_id: string;
  cluster_revision: number;
  action: ChangeParameters['action'];
  status: 'blocked' | 'requires_validation';
  execution_available: false;
  current_version: string | null;
  observed_at: string | null;
  node_count: number | null;
  ready_nodes: number | null;
  checks: Array<{ code: string; label: string; status: 'passed' | 'blocked' | 'unverified' }>;
}

export function checkKubernetesChange(
  clusterId: string, revision: number, change: ChangeParameters, signal?: AbortSignal,
): Promise<ChangePreflight> {
  return request(`/api/v1/kubernetes/clusters/${encodeURIComponent(clusterId)}/change-preflight`, {
    method: 'POST', body: JSON.stringify({ ...change, expected_revision: revision }), signal,
  }, '集群变更预检失败');
}
