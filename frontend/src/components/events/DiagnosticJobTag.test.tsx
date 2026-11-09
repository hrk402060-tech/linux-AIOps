import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { DiagnosticJob } from '../../api/externalAlerts';
import { DiagnosticJobTag } from './DiagnosticJobTag';

const job = {
  id: 'job-1', alert_id: 'alert-1', asset_id: 'asset-1', grant_revision: 2,
  status: 'queued', display_status: 'queued', trigger_type: 'manual', operator: 'operator',
  reason: '人工排查', attempt_count: 0, max_attempts: 3, next_attempt_at: null,
  cancellation_requested_at: null, last_error_code: null, created_at: '', updated_at: '', finished_at: null,
} as DiagnosticJob;

describe('DiagnosticJobTag', () => {
  it.each([
    ['queued', '排队中'], ['running', '只读采集中'], ['retry_wait', '等待重试'],
    ['completed', '证据已采集'], ['partial', '证据不完整'], ['failed', '任务失败'],
    ['cancelled', '已取消'],
  ] as const)('uses the actual %s job state without claiming root cause or recovery', (status, label) => {
    render(<DiagnosticJobTag job={{ ...job, status, display_status: status }} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(/根因已确认|恢复成功/)).not.toBeInTheDocument();
  });

  it('keeps an in-flight cancellation distinct from finished cancellation', () => {
    render(<DiagnosticJobTag job={{ ...job, status: 'running', display_status: 'cancelling', cancellation_requested_at: '2026-10-09T01:00:00Z' }} />);
    expect(screen.getByText('取消中')).toBeInTheDocument();
    expect(screen.queryByText('已取消')).not.toBeInTheDocument();
  });

  it('shows missing diagnostic authorization explicitly instead of a generic failure', () => {
    render(<DiagnosticJobTag job={{ ...job, status: 'failed', display_status: 'failed', last_error_code: 'diagnostic_authorization_required' }} />);
    expect(screen.getByText('待授权')).toBeInTheDocument();
  });
});
