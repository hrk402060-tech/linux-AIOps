import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../../api/auth';
import { AlertNotificationHistory } from './AlertNotificationHistory';

const notice = {
  id: 'notice-1', alert_id: 'alert-1', kind: 'diagnostic_result', status: 'sent', attempts: 1,
  max_attempts: 5, error_code: null, recipients: ['admin@example.com'],
  created_at: '2026-10-21T00:00:00Z', sent_at: '2026-10-21T00:01:00Z',
  next_attempt_at: null, acknowledged_at: null, acknowledged_by: null,
};
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
type Role = AuthUser['role'];
function view(role: Role = 'admin', alertId = 'alert-1', onUpdated = vi.fn()) {
  return <ConfigProvider theme={{ token: { motion: false } }}>
    <AlertNotificationHistory alertId={alertId} currentUser={{ role }} onUpdated={onUpdated} />
  </ConfigProvider>;
}
function setup(item = notice, role: Role = 'admin') {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => response({ items: [item] }));
  vi.stubGlobal('fetch', fetchMock);
  const onUpdated = vi.fn();
  return { fetchMock, onUpdated, ...render(view(role, 'alert-1', onUpdated)) };
}
afterEach(() => vi.unstubAllGlobals());

it('does not acknowledge SMTP acceptance until a human confirms', async () => {
  const { fetchMock, onUpdated } = setup(notice, 'operator');
  await screen.findByText('未确认查看');
  fireEvent.click(screen.getByRole('button', { name: '确认已查看通知 notice-1' }));
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  fetchMock.mockResolvedValueOnce(response({ ...notice, acknowledged_at: notice.sent_at, acknowledged_by: 'operator' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
  await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1));
  expect(await screen.findByText('operator')).toBeInTheDocument();
});

it.each(['operator', 'auditor'] as const)('does not allow %s to retry notification delivery', async (role) => {
  const { fetchMock } = setup({ ...notice, status: 'failed' }, role);
  await screen.findByText('发送失败');
  expect(screen.queryByRole('button', { name: '重试通知 notice-1' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('rejects notification records from another alert', async () => {
  const { fetchMock } = setup({ ...notice, alert_id: 'another-alert' });
  await screen.findByText('通知记录与当前告警不匹配，请刷新重试。');
  expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('resets an open confirmation when the role changes to auditor', async () => {
  const { rerender, onUpdated } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  await screen.findByRole('button', { name: '确认已查看' });
  rerender(view('auditor', 'alert-1', onUpdated));
  await screen.findByText('未确认查看');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
});

it('ignores a late write after switching to another alert', async () => {
  const { fetchMock, rerender, onUpdated } = setup({ ...notice, status: 'failed' });
  await screen.findByText('发送失败');
  let resolve!: (value: ReturnType<typeof response>) => void;
  fetchMock.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  fireEvent.click(screen.getByRole('button', { name: '重试通知 notice-1' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  fetchMock.mockResolvedValue(response({ items: [] }));
  rerender(view('admin', 'alert-2', onUpdated));
  await screen.findByText('暂无通知记录');
  await act(async () => { resolve(response({ ...notice, status: 'pending' })); });
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.queryByText('待发送')).not.toBeInTheDocument();
});

it('ignores a late history response after switching alerts', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise((done) => { resolve = done; }))
    .mockResolvedValue(response({ items: [] })));
  const { rerender } = render(view());
  rerender(view('admin', 'alert-2'));
  await screen.findByText('暂无通知记录');
  await act(async () => { resolve(response({ items: [notice] })); });
  expect(screen.queryByText('admin@example.com')).not.toBeInTheDocument();
});

it('requires an explicit refresh after an uncertain write failure', async () => {
  const { fetchMock, rerender } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  fetchMock.mockRejectedValueOnce(new TypeError('connection interrupted'));
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
  await screen.findByText('connection interrupted');
  expect(screen.queryByRole('dialog', { name: '人工查看确认' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  rerender(view('admin', 'alert-1'));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '人工查看确认' })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '刷新通知记录' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '确认已查看通知 notice-1' })).toBeEnabled());
}, 15_000);

it('rejects a mismatched write result without claiming acknowledgment', async () => {
  const { fetchMock, onUpdated } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  fetchMock.mockResolvedValueOnce(response({ ...notice, id: 'another-notice', acknowledged_at: notice.sent_at }));
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
  await screen.findByText('通知操作结果不匹配，请刷新核实。');
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '确认已查看' })).toBeDisabled();
  expect(screen.getByText('未确认查看')).toBeInTheDocument();
});

it('disables an existing confirmation while refreshing and after loading fails', async () => {
  const { fetchMock } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  let resolve!: (value: ReturnType<typeof response>) => void;
  fetchMock.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  fireEvent.click(screen.getByRole('button', { name: '刷新通知记录' }));
  expect(screen.getByRole('button', { name: '确认已查看' })).toBeDisabled();
  await act(async () => { resolve(response({ detail: '记录读取失败' }, 503)); });
  await screen.findByText('记录读取失败');
  expect(screen.getByRole('button', { name: '确认已查看' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '确认已查看' }));
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('ignores a late acknowledgment after only the role changes', async () => {
  const { fetchMock, rerender, onUpdated } = setup();
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
  let resolve!: (value: ReturnType<typeof response>) => void;
  fetchMock.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
  rerender(view('auditor', 'alert-1', onUpdated));
  await screen.findByText('未确认查看');
  await act(async () => { resolve(response({ ...notice, acknowledged_at: notice.sent_at, acknowledged_by: 'admin' })); });
  expect(onUpdated).not.toHaveBeenCalled();
  expect(screen.queryByText('已记录人工查看确认')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
});

it('does not duplicate a pending retry', async () => {
  const { fetchMock, onUpdated } = setup({ ...notice, status: 'failed' });
  const retry = await screen.findByRole('button', { name: '重试通知 notice-1' });
  let resolve!: (value: ReturnType<typeof response>) => void;
  fetchMock.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  fireEvent.click(retry);
  fireEvent.click(retry);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  await act(async () => { resolve(response({ ...notice, status: 'pending' })); });
  expect(onUpdated).toHaveBeenCalledTimes(1);
  expect(await screen.findByText('待发送')).toBeInTheDocument();
});
