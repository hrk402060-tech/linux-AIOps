import type { PageKey } from '../App';
import { getScanSchedules } from '../api/scan-tasks';
import { getBaselineTasks } from '../api/baselines';
import { getExposureTasks } from '../api/exposures';
import { getCredentialRiskTasks } from '../api/credential-risks';
import { getPatchAdvice, type PatchAdvice } from '../api/patches';
import {
  getAutomationApprovals, getChangeRecords, getExternalIntegrations,
  type AutomationApproval, type ChangeRecord, type ExternalIntegration,
} from '../api/operations';
import type { ScanSchedule } from '../types/scan-task';
import type { BaselineTask } from '../types/baseline';
import type { ExposureTask } from '../types/exposure';
import type { CredentialRiskTask } from '../types/credential-risk';

export interface WorkbenchData {
  schedules: ScanSchedule[] | null;
  baselines: BaselineTask[] | null;
  exposures: ExposureTask[] | null;
  risks: CredentialRiskTask[] | null;
  patches: PatchAdvice[] | null;
  approvals: AutomationApproval[] | null;
  changes: ChangeRecord[] | null;
  integrations: ExternalIntegration[] | null;
}

export interface WorkbenchTask {
  key: string;
  id: string;
  name: string;
  page: PageKey;
  recordTab?: 'approvals' | 'changes';
  category: 'check' | 'change';
  module: string;
  status: string;
  pending: boolean;
  target: string;
  updatedAt: string | null;
  progress: number | null;
  facts: Array<[string, string]>;
  logs: Array<{ message: string; occurred_at: string }>;
  error: string | null;
}

export const moduleEntries = [
  { key: 'schedules', label: '定时扫描', dataKey: 'schedules' },
  { key: 'baselines', label: '基线合规', dataKey: 'baselines' },
  { key: 'exposures', label: '暴露面检测', dataKey: 'exposures' },
  { key: 'credential-risks', label: '弱口令风险', dataKey: 'risks' },
  { key: 'patches', label: '补丁与变更', dataKey: 'patches' },
] as const;

const sourceLabels = ['定时扫描', '基线合规', '暴露面检测', '弱口令风险', '补丁与变更', '审批队列', '变更与回滚', '外部告警接入'];

export async function loadWorkbenchData(previous: WorkbenchData | null): Promise<{ data: WorkbenchData; errors: string[] }> {
  const results = await Promise.allSettled([
    getScanSchedules(), getBaselineTasks(), getExposureTasks(), getCredentialRiskTasks(),
    getPatchAdvice(), getAutomationApprovals(), getChangeRecords(), getExternalIntegrations(),
  ] as const);
  const keep = <T,>(result: PromiseSettledResult<T>, old: T | null | undefined): T | null =>
    result.status === 'fulfilled' ? result.value : old ?? null;
  const data: WorkbenchData = {
    schedules: keep(results[0], previous?.schedules), baselines: keep(results[1], previous?.baselines),
    exposures: keep(results[2], previous?.exposures), risks: keep(results[3], previous?.risks),
    patches: keep(results[4], previous?.patches), approvals: keep(results[5], previous?.approvals),
    changes: keep(results[6], previous?.changes), integrations: keep(results[7], previous?.integrations),
  };
  const errors = results.flatMap((result, index) => result.status === 'rejected' ? [sourceLabels[index]] : []);
  return { data, errors };
}

export const statusLabels: Record<string, string> = {
  enabled: '已启用', disabled: '已停用', queued: '排队中', scanning: '扫描中', assessing: '评估中',
  aggregating: '汇总中', completed: '已完成', failed: '失败', cancelled: '已取消',
  pending_approval: '待确认', pending_verification: '待复扫验证', fixed: '已修复',
  ignored: '已忽略', risk_accepted: '风险已接受', invalid_target: '目标版本无效',
  manual_confirmation: '需人工确认', pending: '待审批', approved: '已批准', rejected: '已拒绝',
  running: '执行中', planned: '计划中', applied: '已应用', rolled_back: '已回滚',
};

export function statusTone(status: string): 'normal' | 'warning' | 'danger' | 'muted' {
  if (['failed', 'invalid_target'].includes(status)) return 'danger';
  if (['pending', 'pending_approval', 'pending_verification', 'manual_confirmation', 'planned'].includes(status)) return 'warning';
  if (['enabled', 'completed', 'fixed', 'approved', 'applied', 'running', 'scanning', 'assessing', 'aggregating'].includes(status)) return 'normal';
  return 'muted';
}

export function formatWorkbenchTime(value: string | null | undefined): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '—';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function scheduledTask(item: ScanSchedule): WorkbenchTask {
  const target = item.asset_snapshot.map((asset) => `${asset.name} ${asset.host}`).join('、') || '未关联服务器';
  return {
    key: `schedules:${item.id}`, id: item.id, name: item.name, page: 'schedules', category: 'check',
    module: '定时扫描', status: item.enabled ? 'enabled' : 'disabled', pending: false,
    target, updatedAt: item.updated_at, progress: null, logs: [], error: null,
    facts: [
      ['目标范围', target], ['执行周期', item.schedule_kind === 'daily' ? `每日 ${item.daily_time}` : `每 ${item.interval_hours} 小时`],
      ['时区', item.timezone], ['最近执行', formatWorkbenchTime(item.last_run_at)],
      ['下次执行', item.enabled ? formatWorkbenchTime(item.next_run_at) : '已停用'],
    ],
  };
}

function checkTask(item: BaselineTask | ExposureTask | CredentialRiskTask, page: PageKey, module: string): WorkbenchTask {
  const target = 'asset_snapshot' in item
    ? item.asset_snapshot.map((asset) => `${asset.name} ${asset.host}`).join('、')
    : item.credential_snapshot.map((credential) => credential.name).join('、');
  return {
    key: `${page}:${item.id}`, id: item.id, name: item.name, page, category: 'check', module,
    status: item.status, pending: false, target: target || '未关联目标', updatedAt: item.updated_at,
    progress: Number.isFinite(item.progress) ? Math.max(0, Math.min(100, item.progress)) : null,
    facts: [
      ['目标范围', target || '未关联目标'], ['开始时间', formatWorkbenchTime(item.started_at)],
      ['完成时间', formatWorkbenchTime(item.finished_at)],
    ],
    logs: item.execution_log ?? [], error: item.error_message,
  };
}

function patchTask(item: PatchAdvice): WorkbenchTask {
  return {
    key: `patches:${item.id}`, id: item.id, name: `${item.asset_name} · ${item.package_name}`,
    page: 'patches', category: 'change', module: '补丁与变更', status: item.status,
    pending: ['pending_approval', 'manual_confirmation'].includes(item.status),
    target: `${item.asset_name} ${item.cve_id} ${item.package_name}`, updatedAt: item.latest_detected_at,
    progress: null, logs: [], error: item.execution_issue ?? item.target_version_issue ?? null,
    facts: [
      ['关联漏洞', item.cve_id], ['当前版本', item.current_version], ['建议版本', item.target_version],
      ['来源', item.source_name], ['最近扫描', item.latest_scan_name], ['回滚预案', item.rollback_plan || '尚未提供'],
    ],
  };
}

function approvalTask(item: AutomationApproval): WorkbenchTask {
  const operations: Record<string, string> = {
    scan: '漏洞扫描', baseline: '基线合规', exposure: '暴露面检测',
    credential_risk: '弱口令风险', read_only_diagnostic: '只读诊断',
  };
  return {
    key: `approvals:${item.id}`, id: item.id, name: item.title, page: 'automation', recordTab: 'approvals',
    category: 'check', module: '审批队列', status: item.status, pending: item.status === 'pending',
    target: item.target_id ? `关联对象 ${item.target_id}` : '未指定目标', updatedAt: item.updated_at,
    progress: null, logs: [], error: null,
    facts: [
      ['申请操作', operations[item.operation_type] ?? item.operation_type], ['关联对象', item.target_id ?? '未指定'],
      ['申请原因', item.reason], ['申请人', item.requested_by], ['审批人', item.approved_by ?? '尚未审批'],
      ['申请时间', formatWorkbenchTime(item.requested_at)], ['完成时间', formatWorkbenchTime(item.completed_at)],
    ],
  };
}

function changeTask(item: ChangeRecord): WorkbenchTask {
  const types = { release: '发布', configuration: '配置', patch: '补丁' };
  return {
    key: `changes:${item.id}`, id: item.id, name: item.title, page: 'automation', recordTab: 'changes',
    category: 'change', module: '变更与回滚', status: item.status, pending: item.status === 'planned',
    target: item.target_id ? `关联对象 ${item.target_id}` : '未指定目标', updatedAt: item.updated_at,
    progress: null, logs: [], error: null,
    facts: [
      ['变更类型', types[item.change_type] ?? item.change_type], ['关联对象', item.target_id ?? '未指定'],
      ['负责人', item.requested_by], ['审批人', item.approved_by ?? '尚未审批'],
      ['回滚预案', item.rollback_plan], ['备注', item.note], ['应用时间', formatWorkbenchTime(item.applied_at)],
      ['回滚时间', formatWorkbenchTime(item.rolled_back_at)],
    ],
  };
}

export function buildWorkbenchTasks(data: WorkbenchData | null): WorkbenchTask[] {
  if (!data) return [];
  return [
    ...(data.schedules ?? []).map(scheduledTask),
    ...(data.baselines ?? []).map((item) => checkTask(item, 'baselines', '基线合规')),
    ...(data.exposures ?? []).map((item) => checkTask(item, 'exposures', '暴露面检测')),
    ...(data.risks ?? []).map((item) => checkTask(item, 'credential-risks', '弱口令风险')),
    ...(data.patches ?? []).map(patchTask),
    ...(data.approvals ?? []).map(approvalTask),
    ...(data.changes ?? []).map(changeTask),
  ].sort((a, b) => {
    const time = (value: string | null) => value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
    return time(b.updatedAt) - time(a.updatedAt) || a.key.localeCompare(b.key);
  });
}

export function filterWorkbenchTasks(tasks: WorkbenchTask[], search: string, category: string, pending: boolean): WorkbenchTask[] {
  const query = search.trim().toLocaleLowerCase();
  return tasks.filter((item) => (category === 'all' || item.category === category)
    && (!pending || item.pending)
    && (!query || [item.name, item.module, item.target, item.id, ...item.logs.map((log) => log.message)]
      .join(' ').toLocaleLowerCase().includes(query)));
}
