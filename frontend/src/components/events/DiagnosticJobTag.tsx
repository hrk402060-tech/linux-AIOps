import { Tag } from 'antd';
import type { DiagnosticJob, DiagnosticJobStatus } from '../../api/externalAlerts';

export const diagnosticJobLabels: Record<DiagnosticJobStatus | 'cancelling', string> = {
  queued: '排队中', running: '只读采集中', retry_wait: '等待重试', completed: '证据已采集',
  partial: '证据不完整', failed: '任务失败', cancelled: '已取消', cancelling: '取消中',
};

export function isActiveDiagnosticJob(job?: DiagnosticJob | null): boolean {
  return Boolean(job && ['queued', 'running', 'retry_wait'].includes(job.status));
}

export function getDiagnosticJobLabel(job: DiagnosticJob): string {
  if (job.status === 'failed' && job.last_error_code === 'diagnostic_authorization_required') return '待授权';
  if (job.cancellation_requested_at && job.status === 'running') return diagnosticJobLabels.cancelling;
  return diagnosticJobLabels[job.display_status] ?? diagnosticJobLabels[job.status];
}

export function DiagnosticJobTag({ job }: { job: DiagnosticJob }) {
  const color = job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red'
    : job.status === 'partial' || job.status === 'retry_wait' || job.cancellation_requested_at ? 'gold'
    : job.status === 'cancelled' ? undefined : 'blue';
  return <Tag color={color}>{getDiagnosticJobLabel(job)}</Tag>;
}
