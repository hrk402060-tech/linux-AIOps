import { request } from './kubernetes';

export type ChangeParameters =
  | { action: 'scale'; additional_nodes: number }
  | { action: 'upgrade'; target_version: string }
  | { action: 'backup'; namespace: string }
  | { action: 'restore'; namespace: string; backup_name: string }
  | { action: 'helm'; namespace: string; release_name: string; chart_id: string; chart_digest: string };

export interface HelmChart {
  id: string; name: string; version: string; digest: string;
}
export interface HelmCatalog { enabled: boolean; charts: HelmChart[] }
export interface HelmResource {
  api_version: string; kind: string; namespace: string; name: string;
}
export interface HelmPreview {
  preflight: ChangePreflight;
  chart: HelmChart;
  release_name: string;
  namespace: string;
  resources: HelmResource[];
  preview_id: string | null;
  preview_expires_at: string | null;
}

export interface HelmApproval {
  id: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'invalidated';
  cluster_revision: number;
  chart: HelmChart;
  resources: HelmResource[];
  namespace: string;
  release_name: string;
  requested_by: string;
  reviewed_by: string | null;
  reason: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  expires_at: string;
  execution_available: false;
  can_review: boolean;
  can_rehearse?: boolean;
  can_execute?: boolean;
}

export interface HelmApprovalList { items: HelmApproval[]; total: number }

export interface HelmAdmissionResult {
  trace_id: string;
  status: 'passed' | 'failed';
  execution_available: false;
  checked: number;
  total: number;
  error_code: string | null;
}

export interface HelmExecutionResult {
  trace_id: string;
  status: 'succeeded' | 'failed' | 'rolled_back' | 'rollback_failed';
  execution_available: true;
  created: number;
  validated: number;
  rollback: 'not_needed' | 'succeeded' | 'failed';
  error_code: string | null;
}

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

export function getHelmCharts(signal?: AbortSignal): Promise<HelmCatalog> {
  return request('/api/v1/kubernetes/helm-charts', { signal }, 'Helm Chart 目录读取失败');
}

export function previewHelmChange(
  clusterId: string, revision: number,
  change: Omit<Extract<ChangeParameters, { action: 'helm' }>, 'action'>,
  signal?: AbortSignal,
): Promise<HelmPreview> {
  return request(`/api/v1/kubernetes/clusters/${encodeURIComponent(clusterId)}/helm-preview`, {
    method: 'POST', body: JSON.stringify({ ...change, expected_revision: revision }), signal,
  }, 'Helm 预览失败', 30_000);
}

function approvalEndpoint(clusterId: string): string {
  return `/api/v1/kubernetes/clusters/${encodeURIComponent(clusterId)}/helm-approvals`;
}

export function getHelmApprovals(clusterId: string, offset: number, signal?: AbortSignal): Promise<HelmApprovalList> {
  return request(`${approvalEndpoint(clusterId)}?offset=${offset}`, { signal }, 'Helm 审批记录加载失败');
}

export function submitHelmApproval(
  clusterId: string, previewId: string, reason: string, signal?: AbortSignal,
): Promise<HelmApproval> {
  return request(approvalEndpoint(clusterId), {
    method: 'POST', body: JSON.stringify({ preview_id: previewId, reason }), signal,
  }, 'Helm 审批申请失败');
}

export function decideHelmApproval(
  clusterId: string, approvalId: string, confirmation: 'yes' | 'no', signal?: AbortSignal,
): Promise<HelmApproval> {
  return request(`${approvalEndpoint(clusterId)}/${encodeURIComponent(approvalId)}/decision`, {
    method: 'POST', body: JSON.stringify({ confirmation }), signal,
  }, 'Helm 审批确认失败');
}

export function runHelmAdmissionRehearsal(
  clusterId: string, approvalId: string, signal?: AbortSignal,
): Promise<HelmAdmissionResult> {
  return request(`${approvalEndpoint(clusterId)}/${encodeURIComponent(approvalId)}/admission-rehearsal`, {
    method: 'POST', body: JSON.stringify({ confirmation: 'yes' }), signal,
  }, '准入演练请求失败', 45_000);
}

export function executeHelmApproval(
  clusterId: string, approvalId: string, signal?: AbortSignal,
): Promise<HelmExecutionResult> {
  return request(`${approvalEndpoint(clusterId)}/${encodeURIComponent(approvalId)}/execute`, {
    method: 'POST', body: JSON.stringify({ confirmation: 'yes' }), signal,
  }, 'Helm 隔离执行请求失败', 60_000);
}
