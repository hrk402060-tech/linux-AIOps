import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { Incident } from '../api/incidents';
import { IncidentDetailDrawer } from './IncidentDetailDrawer';

const incident: Incident = {
  id: 'incident-1', title: '接口超时', description: '人工排查', severity: 'high', status: 'in_progress',
  asset_id: 'asset-1', asset_name: 'node-1', alert_id: 'alert:alert-1', task_id: null,
  operator: 'operator', created_at: '2026-10-21T00:00:00Z',
};
const notice = {
  id: 'notice-1', alert_id: 'alert-1', kind: 'diagnostic_result', status: 'sent', attempts: 1,
  max_attempts: 5, error_code: null, recipients: ['admin@example.com'],
  created_at: incident.created_at, sent_at: incident.created_at, next_attempt_at: null,
  acknowledged_at: null, acknowledged_by: null,
};
const alert = {
  id: 'alert-1', asset_id: incident.asset_id, incident_id: incident.id, title: '接口延迟',
  status: 'in_progress', received_at: incident.created_at,
  latest_analysis: { id: 'analysis-2', status: 'completed', summary: '疑似连接池耗尽' },
  notification_summary: {
    total: 1, sent: 1, failed: 0, blocked: 0, pending: 0, acknowledged: 0,
    latest_diagnostic: { id: notice.id, analysis_id: 'analysis-2', status: 'sent', acknowledged: false },
  },
};
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
function setup(options: { role?: 'admin' | 'operator' | 'auditor'; summary?: object; noticeStatus?: string; mismatch?: boolean } = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return response({ ...notice, acknowledged_by: 'operator', acknowledged_at: incident.created_at });
    if (url.endsWith('/notifications')) return response({ items: [{ ...notice, status: options.noticeStatus ?? 'sent' }] });
    if (url.includes('/recovery-verifications')) return response({ total: 0, items: [],
      options: { asset_enabled: true, metrics: [], probes: [], collection_mode: 'existing_evidence_only' } });
    if (url.startsWith('/api/v1/alerts/')) return response({ ...alert,
      ...(options.mismatch ? { asset_id: 'other-asset' } : {}),
      notification_summary: options.summary ?? alert.notification_summary });
    if (url.endsWith('/timeline')) return response([]);
    return response(incident);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <IncidentDetailDrawer incident={incident} currentUser={{ role: options.role ?? 'admin' }} onClose={vi.fn()} />
  </ConfigProvider>);
  return fetchMock;
}
afterEach(() => vi.unstubAllGlobals());

it('shows delivery and manual-view evidence without automatically posting', async () => {
  const fetchMock = setup();
  const region = within(await screen.findByRole('region', { name: '管理员通知' }));
  expect(region.getByText('已发送 · 待查看确认')).toBeInTheDocument();
  fireEvent.click(region.getByRole('button', { name: '查看管理员通知记录' }));
  await screen.findByText('未确认查看');
  expect(screen.getByText('admin@example.com')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/notifications', expect.any(Object));
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
});

it('does not treat old diagnostic mail as delivery of the latest diagnosis', async () => {
  setup({ summary: { ...alert.notification_summary,
    latest_diagnostic: { ...alert.notification_summary.latest_diagnostic, analysis_id: 'analysis-old' } } });
  await screen.findByText('待定位结果通知');
  expect(screen.getByText(/历史通知不代表本次结果已送达/)).toBeInTheDocument();
});

it('shows notification blocking rather than pretending the mandatory notification succeeded', async () => {
  setup({ summary: { ...alert.notification_summary, total: 0, sent: 0, latest_diagnostic: null } });
  await screen.findByText('尚无通知记录');
  expect(screen.getByText('未形成通知发送证据')).toBeInTheDocument();
});

it('refreshes incident context only after a human acknowledges the mail', async () => {
  const fetchMock = setup({ role: 'operator' });
  fireEvent.click(await screen.findByRole('button', { name: '查看管理员通知记录' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/v1/incidents/incident-1')).toHaveLength(2));
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(fetchMock.mock.calls.some(([url]) => /terminal|diagnostic-jobs/.test(url))).toBe(false);
}, 15_000);

it('keeps auditors read-only while allowing notification evidence to be viewed', async () => {
  setup({ role: 'auditor' });
  fireEvent.click(await screen.findByRole('button', { name: '查看管理员通知记录' }));
  await screen.findByText('未确认查看');
  expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重试通知 notice-1' })).not.toBeInTheDocument();
});

it('does not load notification records for a mismatched linked alert', async () => {
  const fetchMock = setup({ mismatch: true });
  await screen.findByText('关联告警与当前故障不匹配，已禁止恢复验证。');
  expect(screen.queryByRole('button', { name: '查看管理员通知记录' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/notifications'))).toBe(false);
});
