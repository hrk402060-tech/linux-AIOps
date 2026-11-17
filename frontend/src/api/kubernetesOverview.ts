import { request } from './kubernetes';

export type KubernetesCollectionState = 'unobserved' | 'queued' | 'running' | 'complete' | 'partial' | 'failed' | 'cancelled';
export interface KubernetesResourceSummary {
  state: 'complete' | 'partial' | 'failed' | 'unobserved';
  observed_count: number;
  total: number | null;
  error_code: string | null;
}
export interface KubernetesMetrics {
  state: 'unavailable' | 'complete' | 'partial';
  cpu_cores: number | null;
  cpu_allocatable_cores: number | null;
  cpu_percent: number | null;
  memory_bytes: number | null;
  memory_allocatable_bytes: number | null;
  memory_percent: number | null;
  covered_nodes: number;
  total_nodes: number | null;
  sample_time: string | null;
  window: string | null;
  source: 'metrics.k8s.io';
  error_code: string | null;
}
export interface KubernetesOverview {
  cluster_id: string;
  collection_state: KubernetesCollectionState;
  freshness: 'unobserved' | 'fresh' | 'stale' | 'unavailable';
  run_id: string | null;
  observed_at: string | null;
  last_success_at: string | null;
  counts: Record<'nodes' | 'nodes_ready' | 'nodes_not_ready' | 'namespaces' | 'pods' |
    'pods_abnormal' | 'deployments' | 'statefulsets' | 'daemonsets' | 'services' | 'warning_events', number | null>;
  resources: Record<string, KubernetesResourceSummary>;
  metrics: KubernetesMetrics;
  warnings: Array<{ reason: string; target: string; namespace: string | null; count: number | null; last_seen: string | null }>;
  errors: Array<{ resource: string; code: string; message: string }>;
}
export interface KubernetesRefresh {
  cluster_id: string;
  run_id: string;
  state: 'queued' | 'running';
  merged: boolean;
}
const root = '/api/v1/kubernetes/clusters';

export function getKubernetesOverview(id: string, signal?: AbortSignal): Promise<KubernetesOverview> {
  return request<KubernetesOverview>(`${root}/${encodeURIComponent(id)}/overview`, { signal });
}
export function refreshKubernetesOverview(id: string, signal?: AbortSignal): Promise<KubernetesRefresh> {
  return request<KubernetesRefresh>(`${root}/${encodeURIComponent(id)}/refresh`, { method: 'POST', signal });
}
