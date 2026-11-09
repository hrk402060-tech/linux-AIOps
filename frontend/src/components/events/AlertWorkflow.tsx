import { Activity, Bell, ClipboardList, Inbox, Search, UserRound } from 'lucide-react';
import type { NotificationSummary } from '../../api/alertNotifications';
import type { ExternalAlert } from '../../api/externalAlerts';
import { getDiagnosticJobLabel, isActiveDiagnosticJob } from './DiagnosticJobTag';
import { recoveryLabels } from './RecoveryVerificationPanel';
import './AlertWorkflow.css';

type Tone = 'complete' | 'blocked' | 'pending' | 'active';

export function AlertWorkflow({ alert, recordCount }: { alert: ExternalAlert; recordCount: number }) {
  const analysis = alert.latest_analysis;
  const authorizationBlocked = !analysis || analysis.error_code === 'diagnostic_authorization_required';
  const currentEvidence = ['readonly-linux-2026.10.07', 'readonly-kubernetes-2026.10.15',
    'readonly-docker-2026.10.15', 'readonly-mysql-2026.10.22'].includes(analysis?.policy_version ?? '');
  const activeJob = isActiveDiagnosticJob(alert.latest_job) ? alert.latest_job : null;
  const currentJob = alert.latest_job;
  const diagnosis = currentJob ? getDiagnosticJobLabel(currentJob) : authorizationBlocked ? '待授权' : analysis?.status === 'partial' ? '部分采集失败'
    : analysis?.status === 'completed' ? (currentEvidence ? '已采集只读证据' : '已有历史证据')
    : analysis?.status === 'running' ? '定位中' : '定位失败';
  const treatment = {
    open: '待人工确认', acknowledged: '已确认', in_progress: '人工处理中',
    resolved: '人工登记完成', closed: '人工已关闭',
  }[alert.status];
  const labels = alert.payload.labels;
  const previewOnly = labels !== null && typeof labels === 'object'
    && 'preview_only' in labels && ['true', true].includes(labels.preview_only as string | boolean);
  const stages = [
    { name: '告警接收', value: '已接收', detail: new Date(alert.received_at).toLocaleString(), icon: Inbox, tone: 'complete' },
    { name: '告警定位', value: diagnosis, detail: activeJob ? '任务未完成，不代表根因或恢复' : currentEvidence ? '证据需人工研判，不代表根因或恢复' : '专用只读授权待配置', icon: Search, tone: activeJob ? 'active' : authorizationBlocked || ['failed', 'partial'].includes(analysis?.status ?? '') ? 'blocked' : 'active' },
    { name: '管理员通知', icon: Bell, ...getNotificationStage(alert.notification_summary, analysis, Boolean(activeJob)) },
    { name: '人工处理', value: treatment, detail: '以人工处置记录为准', icon: UserRound, tone: alert.status === 'open' ? 'pending' : 'active' },
    { name: '恢复验证', value: alert.latest_recovery ? recoveryLabels[alert.latest_recovery.status] : '待验证', detail: alert.latest_recovery?.last_evaluation?.summary ?? '尚无监控或拨测恢复证据', icon: Activity, tone: alert.latest_recovery?.status === 'verified' ? 'complete' : ['failed', 'blocked'].includes(alert.latest_recovery?.status ?? '') ? 'blocked' : 'pending' },
    { name: '处理记录', value: `${recordCount} 条记录`, detail: '操作人、时间及关联证据', icon: ClipboardList, tone: 'complete' },
  ];
  return <section className="alert-workflow" aria-label="告警处置流程">
    <div className="alert-workflow-heading"><h3>处置流程</h3>{previewOnly ? <span className="alert-preview-label">演示告警 · 非生产证据</span> : null}</div>
    <ol className="alert-workflow-stages">{stages.map(({ name, value, detail, icon: Icon, tone }, index) => <li key={name} aria-label={name} data-tone={tone as Tone}>
      <span className="alert-workflow-icon"><Icon size={17} /></span>
      <div><small>{index + 1}. {name}</small><strong>{value}</strong><span>{detail}</span></div>
    </li>)}</ol>
  </section>;
}

export function getNotificationStage(
  summary?: NotificationSummary, analysis: ExternalAlert['latest_analysis'] = null, diagnosticActive = false,
): { value: string; detail: string; tone: Tone } {
  if (!summary || !summary.total) return { value: '尚无通知记录', detail: '未形成通知发送证据', tone: 'blocked' };
  const detail = `${summary.sent} / ${summary.total} 已发送 · ${summary.acknowledged} 已确认查看`;
  if (summary.failed) return { value: '通知失败', detail: `${detail} · ${summary.failed} 条需人工升级`, tone: 'blocked' };
  if (summary.blocked) return { value: '通知受阻', detail: `${detail} · ${summary.blocked} 条受阻`, tone: 'blocked' };
  const latest = summary.latest_diagnostic;
  const diagnosticFinished = analysis && ['completed', 'partial', 'failed', 'cancelled'].includes(analysis.status);
  if (diagnosticActive || analysis?.status === 'running'
    || (diagnosticFinished && (!analysis.id || latest?.analysis_id !== analysis.id))) {
    return { value: '待定位结果通知', detail: `${detail} · 历史通知不代表本次结果已送达`, tone: 'pending' };
  }
  if (latest?.status === 'failed') return { value: '通知失败', detail, tone: 'blocked' };
  if (latest?.status === 'blocked') return { value: '通知受阻', detail, tone: 'blocked' };
  if (summary.pending || summary.sent !== summary.total) return { value: '待发送', detail, tone: 'pending' };
  if (latest && latest.status !== 'sent') return { value: '待发送', detail, tone: 'pending' };
  if (summary.acknowledged !== summary.total || (latest && !latest.acknowledged)) return { value: '已发送 · 待查看确认', detail, tone: 'active' };
  return { value: '已人工确认查看', detail, tone: 'complete' };
}
