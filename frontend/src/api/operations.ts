import { apiFetch, parseApiResponse } from './client';

export interface OperationsOverview {
  approvals: Record<string, number>;
  tasks: Record<string, number>;
  read_only_default: boolean;
  direct_execution: boolean;
  message: string;
}

export interface AutomationApproval {
  id: string;
  operation_type: string;
  target_id: string | null;
  title: string;
  status: 'pending' | 'approved' | 'rejected' | 'running' | 'completed' | 'failed';
  requested_by: string;
  approved_by: string | null;
  reason: string;
  result_summary: Record<string, unknown> | null;
  requested_at: string;
  approved_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface ChangeRecord {
  id: string;
  change_type: 'release' | 'configuration' | 'patch';
  target_type: string;
  target_id: string | null;
  title: string;
  status: 'planned' | 'approved' | 'applied' | 'rolled_back' | 'cancelled';
  requested_by: string;
  approved_by: string | null;
  rollback_plan: string;
  note: string;
  created_at: string;
  updated_at: string;
  applied_at: string | null;
  rolled_back_at: string | null;
}

export interface ExternalIntegration {
  id: string;
  name: string;
  source_type: string;
  status: 'active' | 'disabled';
  secret_masked: string;
  updated_at: string;
  testable: boolean;
}

async function read<T>(response: Response, fallback: string): Promise<T> {
  return parseApiResponse<T>(response, fallback);
}

export async function getOperationsOverview(): Promise<OperationsOverview> {
  return read(await apiFetch('/api/v1/operations/overview'), '自动化概览加载失败');
}

export async function getAutomationApprovals(): Promise<AutomationApproval[]> {
  return read(await apiFetch('/api/v1/operations/approvals'), '审批列表加载失败');
}

export async function getChangeRecords(): Promise<ChangeRecord[]> {
  return read(await apiFetch('/api/v1/operations/changes'), '变更记录加载失败');
}

export async function getExternalIntegrations(): Promise<ExternalIntegration[]> {
  return read(await apiFetch('/api/v1/operations/integrations'), '外部接入状态加载失败');
}
