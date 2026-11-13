import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { EventsPage } from './EventsPage';

vi.mock('../components/events/RecoveryVerificationPanel', () => ({ RecoveryVerificationPanel: () => null }));
vi.mock('../components/events/DiagnosticJobHistoryPanel', () => ({ DiagnosticJobHistoryPanel: () => null }));
vi.mock('../components/events/AlertNotificationHistory', () => ({ AlertNotificationHistory: () => null }));

const user = { id: 'user-1', username: 'admin', display_name: '管理员', role: 'admin' as const,
  enabled: true, must_change_password: false, created_at: '', last_login_at: null };
const alert = { id: 'alert-off-page', title: '指定服务告警', asset_id: 'asset-1', asset_name: '服务节点',
  connector_id: 'source-1', severity: 'high', status: 'open', payload: {}, latest_analysis: null,
  latest_job: null, received_at: '2026-11-13T00:00:00Z' };
const response = (body: unknown, status = 200) => Promise.resolve({
  ok: status < 400, status, json: async () => body,
} as Response);

function setup(detailStatus = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') throw new Error('Navigation must be read-only');
    const path = new URL(String(input), 'http://localhost').pathname;
    if (path === '/api/v1/assets') return response([{ id: alert.asset_id, name: alert.asset_name, host: '192.0.2.1', lifecycle_status: 'enabled' }]);
    if (path === '/api/v1/alerts') return response({ total: 40, items: [] });
    if (path === '/api/v1/events') return response({ total: 0, items: [] });
    if (path === '/api/v1/monitoring/overview') return response({ assets: [] });
    if (path === `/api/v1/alerts/${alert.id}`) return response(detailStatus === 200 ? alert : { detail: '目标告警不存在或无权访问' }, detailStatus);
    if (path.endsWith('/timeline') || path.endsWith('/analyses')) return response([]);
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

it('opens the exact off-page alert, preserves the asset filter, and waits for manual handling', async () => {
  const fetchMock = setup();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={user}
    initialAssetId={alert.asset_id} initialAlertId={alert.id} initialTab="external" /></ConfigProvider>);
  const detail = within(await screen.findByRole('dialog', { name: /指定服务告警/ }));
  await waitFor(() => expect(detail.getByRole('button', { name: '详情推进 指定服务告警' })).toBeEnabled());
  const listRequest = fetchMock.mock.calls.find(([input]) => new URL(String(input), 'http://localhost').pathname === '/api/v1/alerts');
  expect(new URL(String(listRequest?.[0]), 'http://localhost').searchParams.get('asset_id')).toBe(alert.asset_id);
  fireEvent.click(detail.getByRole('button', { name: '详情推进 指定服务告警' }));
  expect(await screen.findByRole('button', { name: '确认推进' })).toBeDisabled();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
});

it.each([403, 404])('shows a clear error for %s without opening a different alert', async (status) => {
  setup(status);
  render(<EventsPage currentUser={user} initialAssetId={alert.asset_id} initialAlertId={alert.id} initialTab="external" />);
  expect(await screen.findByText('目标告警不存在或无权访问')).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('accepts a new navigation target after the tabs were already mounted', async () => {
  const fetchMock = setup();
  const { rerender } = render(<EventsPage currentUser={user} />);
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/events?'))).toBe(true));
  rerender(<EventsPage currentUser={user} initialAssetId={alert.asset_id} initialAlertId={alert.id} initialTab="external" />);
  await screen.findByRole('dialog', { name: /指定服务告警/ });
  expect(screen.getByRole('tab', { name: '外部告警研判' })).toHaveAttribute('aria-selected', 'true');
});
