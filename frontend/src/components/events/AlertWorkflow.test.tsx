import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExternalAlert } from '../../api/externalAlerts';
import type { DiagnosticJob } from '../../api/externalAlerts';
import { AlertWorkflow } from './AlertWorkflow';

const alert = {
  id: 'alert-1', title: 'CPU alert', status: 'open', payload: {},
  received_at: '2026-10-03T01:00:00Z', latest_analysis: null,
} as ExternalAlert;

const stage = (name: string) => within(screen.getByRole('listitem', { name }));

describe('AlertWorkflow', () => {
  it('does not substitute a recovery notification for the mandatory diagnosis notification', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: { status: 'completed' } as ExternalAlert['latest_analysis'],
      notification_summary: { total: 2, sent: 2, acknowledged: 2, pending: 0, failed: 0, blocked: 0, diagnostic_results: 0 },
    }} recordCount={5} />);
    expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
  });

  it('projects verification separately from manual status without claiming full service recovery', () => {
    render(<AlertWorkflow alert={{ ...alert, status: 'in_progress', latest_recovery: {
      status: 'verified', last_evaluation: { summary: '仅 CPU 指标在本次观察窗口通过' },
    } as ExternalAlert['latest_recovery'] }} recordCount={5} />);
    expect(stage('恢复验证').getByText('检查通过')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('仅 CPU 指标在本次观察窗口通过')).toBeInTheDocument();
    expect(stage('人工处理').getByText('人工处理中')).toBeInTheDocument();
  });

  it.each([
    ['queued', '排队中'], ['running', '只读采集中'], ['retry_wait', '等待重试'],
  ] as const)('projects active %s jobs over historical completed evidence', (status, label) => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'completed', error_code: null, policy_version: 'readonly-linux-2026.10.07',
    } as ExternalAlert['latest_analysis'], latest_job: {
      status, display_status: status, cancellation_requested_at: null,
    } as DiagnosticJob }} recordCount={3} />);
    expect(stage('告警定位').getByText(label)).toBeInTheDocument();
    expect(stage('告警定位').queryByText('已采集只读证据')).not.toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('does not report a requested running cancellation as complete', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_job: {
      status: 'running', display_status: 'cancelling', cancellation_requested_at: '2026-10-09T01:00:00Z',
    } as DiagnosticJob }} recordCount={3} />);
    expect(stage('告警定位').getByText('取消中')).toBeInTheDocument();
    expect(stage('告警定位').queryByText('已取消')).not.toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('shows authorization and mandatory notification blockers without pretending success', () => {
    render(<AlertWorkflow alert={alert} recordCount={2} />);
    expect(stage('告警定位').getByText('待授权')).toBeInTheDocument();
    expect(stage('管理员通知').getByText('尚无通知记录')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
    expect(stage('处理记录').getByText('2 条记录')).toBeInTheDocument();
  });

  it('distinguishes manually resolved or closed alerts from verified recovery', () => {
    const { rerender } = render(<AlertWorkflow alert={{ ...alert, status: 'resolved' }} recordCount={4} />);
    expect(stage('人工处理').getByText('人工登记完成')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
    expect(screen.queryByText('恢复已验证')).not.toBeInTheDocument();
    rerender(<AlertWorkflow alert={{ ...alert, status: 'closed' }} recordCount={5} />);
    expect(stage('人工处理').getByText('人工已关闭')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('does not treat historical completed diagnostics as authorization or notification proof', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'completed', error_code: null,
    } as ExternalAlert['latest_analysis'] }} recordCount={3} />);
    expect(stage('告警定位').getByText('已有历史证据')).toBeInTheDocument();
    expect(stage('告警定位').getByText('专用只读授权待配置')).toBeInTheDocument();
    expect(stage('管理员通知').getByText('尚无通知记录')).toBeInTheDocument();
  });

  it('labels preview-only alerts without claiming real collection', () => {
    render(<AlertWorkflow alert={{ ...alert, payload: { labels: { preview_only: 'true' } } }} recordCount={2} />);
    expect(screen.getByText('演示告警 · 非生产证据')).toBeInTheDocument();
    expect(stage('告警定位').getByText('待授权')).toBeInTheDocument();
  });

  it('shows partial collection as incomplete evidence and still requires diagnostic notification', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'partial', error_code: 'diagnostic_action_failed',
    } as ExternalAlert['latest_analysis'], notification_summary: {
      total: 1, sent: 1, acknowledged: 1, pending: 0, failed: 0, blocked: 0,
    } }} recordCount={3} />);
    expect(stage('告警定位').getByText('部分采集失败')).toBeInTheDocument();
    expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('describes current Linux gateway results as evidence rather than proven root cause or recovery', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'completed', error_code: null, policy_version: 'readonly-linux-2026.10.07',
    } as ExternalAlert['latest_analysis'] }} recordCount={3} />);
    expect(stage('告警定位').getByText('已采集只读证据')).toBeInTheDocument();
    expect(stage('告警定位').getByText('证据需人工研判，不代表根因或恢复')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('describes current Docker gateway results as evidence rather than a proven root cause', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'completed', error_code: null, policy_version: 'readonly-docker-2026.10.15',
    } as ExternalAlert['latest_analysis'], notification_summary: {
      total: 1, sent: 1, acknowledged: 1, pending: 0, failed: 0, blocked: 0, diagnostic_results: 0,
    } }} recordCount={3} />);
    expect(stage('告警定位').getByText('已采集只读证据')).toBeInTheDocument();
    expect(stage('告警定位').getByText('证据需人工研判，不代表根因或恢复')).toBeInTheDocument();
    expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
  });

  it('retains historical completed result wording without claiming new gateway collection', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: {
      status: 'completed', error_code: null, policy_version: 'v1',
    } as ExternalAlert['latest_analysis'] }} recordCount={2} />);
    expect(stage('告警定位').getByText('已有历史证据')).toBeInTheDocument();
  });

  it.each([
    [{ total: 2, sent: 1, failed: 0, blocked: 1, pending: 0, acknowledged: 0 }, '通知受阻'],
    [{ total: 2, sent: 1, failed: 1, blocked: 0, pending: 0, acknowledged: 0 }, '通知失败'],
    [{ total: 2, sent: 1, failed: 0, blocked: 0, pending: 1, acknowledged: 0 }, '待发送'],
  ] as const)('does not complete mandatory notification while outstanding: %s', (summary, label) => {
    render(<AlertWorkflow alert={{ ...alert, notification_summary: summary }} recordCount={2} />);
    expect(stage('管理员通知').getByText(label)).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '管理员通知' })).not.toHaveAttribute('data-tone', 'complete');
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('distinguishes all messages sent from a human confirming receipt', () => {
    const summary = { total: 2, sent: 2, failed: 0, blocked: 0, pending: 0, acknowledged: 0 };
    const { rerender } = render(<AlertWorkflow alert={{ ...alert, notification_summary: summary }} recordCount={2} />);
    expect(stage('管理员通知').getByText('已发送 · 待查看确认')).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '管理员通知' })).not.toHaveAttribute('data-tone', 'complete');
    rerender(<AlertWorkflow alert={{ ...alert, notification_summary: { ...summary, acknowledged: 2 } }} recordCount={2} />);
    expect(stage('管理员通知').getByText('已人工确认查看')).toBeInTheDocument();
    expect(stage('恢复验证').getByText('待验证')).toBeInTheDocument();
  });

  it('requires the latest diagnostic notification instead of historical counts', () => {
    const summary = {
      total: 3, sent: 3, failed: 0, blocked: 0, pending: 0, acknowledged: 3,
      diagnostic_results: 2,
    };
    const { rerender } = render(<AlertWorkflow alert={{ ...alert,
      latest_analysis: { id: 'analysis-3', status: 'completed' } as ExternalAlert['latest_analysis'],
      notification_summary: summary,
    }} recordCount={2} />);
    expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
    rerender(<AlertWorkflow alert={{ ...alert,
      latest_analysis: { id: 'analysis-3', status: 'completed' } as ExternalAlert['latest_analysis'],
      notification_summary: { ...summary, latest_diagnostic: {
        id: 'notification-3', analysis_id: 'analysis-3', status: 'sent',
        error_code: null, acknowledged: false,
      } },
    }} recordCount={2} />);
    expect(stage('管理员通知').getByText('已发送 · 待查看确认')).toBeInTheDocument();
  });

  it('blocks the workflow when the latest diagnostic notification is blocked', () => {
    render(<AlertWorkflow alert={{ ...alert,
      latest_analysis: { id: 'analysis-4', status: 'failed' } as ExternalAlert['latest_analysis'],
      notification_summary: {
        total: 3, sent: 2, failed: 0, blocked: 1, pending: 0, acknowledged: 2,
        latest_diagnostic: {
          id: 'notification-4', analysis_id: 'analysis-4', status: 'blocked',
          error_code: 'notification_not_configured', acknowledged: false,
        },
      },
    }} recordCount={2} />);
    expect(stage('管理员通知').getByText('通知受阻')).toBeInTheDocument();
  });

  it.each(['queued', 'running', 'retry_wait'] as const)(
    'does not reuse confirmed history while a new diagnosis is %s', (status) => {
      render(<AlertWorkflow alert={{ ...alert,
        latest_analysis: { id: 'old-analysis', status: 'completed' } as ExternalAlert['latest_analysis'],
        latest_job: { status, display_status: status } as DiagnosticJob,
        notification_summary: {
          total: 2, sent: 2, failed: 0, blocked: 0, pending: 0, acknowledged: 2,
          latest_diagnostic: {
            id: 'old-notice', analysis_id: 'old-analysis', status: 'sent',
            error_code: null, acknowledged: true,
          },
        },
      }} recordCount={3} />);
      expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
      expect(screen.getByRole('listitem', { name: '管理员通知' })).toHaveAttribute('data-tone', 'pending');
    },
  );

  it.each(['completed', 'partial', 'failed', 'cancelled'] as const)(
    'requires matching notification evidence for the latest %s result', (status) => {
      render(<AlertWorkflow alert={{ ...alert,
        latest_analysis: { id: 'new-analysis', status } as ExternalAlert['latest_analysis'],
        notification_summary: {
          total: 2, sent: 2, failed: 0, blocked: 0, pending: 0, acknowledged: 2,
          latest_diagnostic: {
            id: 'old-notice', analysis_id: 'old-analysis', status: 'sent',
            error_code: null, acknowledged: true,
          },
        },
      }} recordCount={3} />);
      expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
    },
  );

  it('does not complete a diagnostic alert from its received notification alone', () => {
    render(<AlertWorkflow alert={{ ...alert, latest_analysis: { status: 'failed', error_code: 'diagnostic_authorization_required' } as ExternalAlert['latest_analysis'], notification_summary: { total: 1, sent: 1, failed: 0, blocked: 0, pending: 0, acknowledged: 1 } }} recordCount={2} />);
    expect(stage('管理员通知').getByText('待定位结果通知')).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: '管理员通知' })).not.toHaveAttribute('data-tone', 'complete');
  });
});
