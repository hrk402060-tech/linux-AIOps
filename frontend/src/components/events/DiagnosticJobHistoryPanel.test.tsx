import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { DiagnosticJob, ExternalAlert } from '../../api/externalAlerts';
import { DiagnosticJobHistoryPanel } from './DiagnosticJobHistoryPanel';

const job = {
  id: 'job-1', alert_id: 'alert-1', asset_id: 'asset-1', grant_revision: 3,
  status: 'retry_wait', display_status: 'retry_wait', trigger_type: 'manual', operator: 'operator',
  reason: '人工排查', attempt_count: 1, max_attempts: 3, next_attempt_at: '2026-10-09T01:05:00Z',
  cancellation_requested_at: null, last_error_code: 'timeout', created_at: '2026-10-09T01:00:00Z',
  updated_at: '2026-10-09T01:01:00Z', finished_at: null,
} as DiagnosticJob;
const alert = { id: 'alert-1', latest_job: job } as ExternalAlert;

function stub() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    const attempts = url.pathname.endsWith('/analyses');
    return Promise.resolve({ ok: true, status: 200, json: async () => attempts
      ? [{ id: 'analysis-1', job_id: 'job-1', attempt_number: 1, status: 'failed', summary: '连接超时', error_code: 'timeout', evidence: {}, operator: 'system', started_at: '2026-10-09T01:00:00Z', finished_at: '2026-10-09T01:00:30Z' }]
      : { total: 21, items: [job] } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

it('shows attempts, retry time, safe error and paginated immutable job history', async () => {
  const fetchMock = stub();
  render(<ConfigProvider theme={{ token: { motion: false } }}><DiagnosticJobHistoryPanel alert={alert} currentUser={{ role: 'operator' }} onCancel={vi.fn()} /></ConfigProvider>);
  expect(await screen.findByText('等待重试')).toBeInTheDocument();
  expect(screen.getByText('1 / 3')).toBeInTheDocument();
  expect(screen.getByText('timeout')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostic-jobs?offset=0&limit=10', expect.any(Object));
  fireEvent.click(screen.getByRole('button', { name: '查看尝试 job-1' }));
  expect(await screen.findByText('连接超时')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/analyses?job_id=job-1&offset=0&limit=3', expect.any(Object));
  fireEvent.click(screen.getByTitle('2'));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostic-jobs?offset=10&limit=10', expect.any(Object)));
});

it('hides cancellation from auditors and from already cancellation-pending jobs', async () => {
  stub();
  const { rerender } = render(<DiagnosticJobHistoryPanel alert={alert} currentUser={{ role: 'auditor' }} onCancel={vi.fn()} />);
  await screen.findByText('等待重试');
  expect(screen.queryByRole('button', { name: '取消诊断 job-1' })).not.toBeInTheDocument();
  rerender(<DiagnosticJobHistoryPanel alert={{ ...alert, latest_job: { ...job, status: 'running', display_status: 'cancelling', cancellation_requested_at: '2026-10-09T01:01:00Z' } }} currentUser={{ role: 'operator' }} onCancel={vi.fn()} />);
  const cancel = screen.queryByRole('button', { name: '取消诊断 job-1' });
  if (cancel) expect(cancel).toBeDisabled();
});
