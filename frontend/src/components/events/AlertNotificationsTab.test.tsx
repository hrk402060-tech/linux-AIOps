import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AlertNotificationsTab } from './AlertNotificationsTab';

const config = {
  configured: true, enabled: true, host: 'smtp.example.com', port: 587, security: 'starttls',
  username: 'smtp-user', sender: 'alerts@example.com', recipients: ['admin@example.com'],
  password_configured: true, updated_at: '2026-10-03T01:00:00Z',
};
const notification = {
  id: 'notice-1', alert_id: 'alert-1', kind: 'diagnostic_result', status: 'sent', attempts: 1,
  max_attempts: 5, error_code: null, recipients: ['admin@example.com'],
  created_at: '2026-10-03T01:00:00Z', sent_at: '2026-10-03T01:01:00Z',
  next_attempt_at: null, acknowledged_at: null, acknowledged_by: null,
};
const response = (body: unknown, status = 200) => Promise.resolve({
  ok: status >= 200 && status < 300, status, json: async () => body,
});
function stubApi(overrides: Record<string, unknown> = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const route = `${init?.method ?? 'GET'} ${url.pathname}`;
    if (route in overrides) return response(overrides[route]);
    if (route === 'GET /api/v1/alert-notification-config') return response(config);
    if (route === 'PATCH /api/v1/alert-notification-config') return response(config);
    if (route === 'GET /api/v1/alert-notifications') return response({ total: 1, items: [notification] });
    if (route === 'POST /api/v1/alert-notifications/notice-1/acknowledge') return response({ ...notification, acknowledged_at: '2026-10-03T01:02:00Z', acknowledged_by: 'operator' });
    if (route === 'POST /api/v1/alert-notifications/notice-1/retry') return response({ ...notification, status: 'pending' });
    throw new Error(`Unexpected notification API: ${route}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
function show(role: 'admin' | 'operator' | 'auditor') {
  render(<ConfigProvider theme={{ token: { motion: false } }}><AlertNotificationsTab currentUser={{ role }} /></ConfigProvider>);
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('AlertNotificationsTab', () => {
  it('shows configuration without retrieving a password and omits blank password on save', async () => {
    const fetchMock = stubApi();
    show('admin');
    const host = await screen.findByLabelText('SMTP 服务器');
    await waitFor(() => expect(host).toHaveValue('smtp.example.com'));
    expect(screen.getByLabelText('SMTP 密码')).toHaveValue('');
    expect(screen.getByPlaceholderText('********')).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-notification-config', expect.objectContaining({ method: 'PATCH' })));
    const body = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({ enabled: true, host: 'smtp.example.com', port: 587, security: 'starttls', username: 'smtp-user', sender: 'alerts@example.com', recipients: ['admin@example.com'] });
    expect(String(body)).not.toContain('password');
  });

  it('submits a new password explicitly then clears the input after successful save', async () => {
    const fetchMock = stubApi();
    show('admin');
    await waitFor(() => expect(screen.getByLabelText('SMTP 服务器')).toHaveValue('smtp.example.com'));
    fireEvent.change(screen.getByLabelText('SMTP 密码'), { target: { value: 'test-only-mail-secret' } });
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-notification-config', expect.objectContaining({ body: expect.stringContaining('test-only-mail-secret') })));
    await waitFor(() => expect(screen.getByLabelText('SMTP 密码')).toHaveValue(''));
    expect(screen.queryByText('test-only-mail-secret')).not.toBeInTheDocument();
  });

  it('rejects malformed addresses and more than 20 recipients before requesting save', async () => {
    const fetchMock = stubApi();
    show('admin');
    await waitFor(() => expect(screen.getByLabelText('SMTP 服务器')).toHaveValue('smtp.example.com'));
    fireEvent.change(screen.getByLabelText('收件人'), { target: { value: 'not-an-address' } });
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    expect(await screen.findByText('收件人邮箱格式不正确', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
    fireEvent.change(screen.getByLabelText('收件人'), { target: { value: Array.from({ length: 21 }, (_, i) => `admin${i}@example.com`).join('\n') } });
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    expect(await screen.findByText('最多配置 20 个收件人', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });

  it.each(['operator', 'auditor'] as const)('keeps SMTP configuration read-only for %s', async (role) => {
    const warning = vi.spyOn(console, 'error');
    stubApi();
    show(role);
    expect(await screen.findByText('smtp.example.com')).toBeInTheDocument();
    expect(screen.queryByLabelText('SMTP 密码')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存通知配置' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试通知 notice-1' })).not.toBeInTheDocument();
    if (role === 'auditor') expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
    expect(warning.mock.calls.some((args) => args.some((value) => String(value).includes('not connected to any Form')))).toBe(false);
    warning.mockRestore();
  });

  it('requires human acknowledgment after SMTP acceptance and records the actor', async () => {
    const fetchMock = stubApi();
    show('operator');
    expect(await screen.findByText('已发送')).toBeInTheDocument();
    expect(screen.getByText('未确认查看')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/acknowledge'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '确认已查看通知 notice-1' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-notifications/notice-1/acknowledge', expect.objectContaining({ method: 'POST' })));
    expect(await screen.findByText(/operator/)).toBeInTheDocument();
  });

  it('allows admins to retry failed delivery but never marks it sent from the click', async () => {
    const failed = { ...notification, status: 'failed', error_code: 'smtp_auth_failed', sent_at: null };
    let current = failed;
    const fetchMock = stubApi();
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname === '/api/v1/alert-notifications' && !init?.method) {
        return response({ total: 1, items: [current] });
      }
      if (url.pathname.endsWith('/notice-1/retry') && init?.method === 'POST') {
        current = { ...failed, status: 'pending' };
        return response(current);
      }
      return normal(input, init);
    });
    show('admin');
    expect(await screen.findByText('发送失败')).toBeInTheDocument();
    expect(screen.getByText('smtp_auth_failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试通知 notice-1' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-notifications/notice-1/retry', expect.objectContaining({ method: 'POST' })));
    expect(await screen.findByText('待发送', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText('已发送', { selector: '.ant-tag' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认已查看通知 notice-1' })).not.toBeInTheDocument();
  });

  it('shows loading errors instead of falsely presenting an unconfigured channel', async () => {
    const fetchMock = stubApi();
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => String(input) === '/api/v1/alert-notification-config'
      ? response({ detail: '通知配置暂时不可用' }, 503) : normal(input, init));
    show('admin');
    expect(await screen.findByText('通知配置暂时不可用')).toBeInTheDocument();
    expect(screen.queryByLabelText('SMTP 密码')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存通知配置' })).not.toBeInTheDocument();
  });

  it('rejects header injection in sender before saving', async () => {
    const fetchMock = stubApi();
    show('admin');
    await waitFor(() => expect(screen.getByLabelText('SMTP 服务器')).toHaveValue('smtp.example.com'));
    fireEvent.change(screen.getByLabelText('发件人'), { target: { value: 'alerts@example.com Bcc:other@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    expect(await screen.findByText('发件人邮箱格式不正确')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
  });

  it('shows acknowledge errors without inventing a human acknowledgment', async () => {
    const fetchMock = stubApi();
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => String(input).endsWith('/acknowledge')
      ? response({ detail: '查看确认暂时不可用' }, 503) : normal(input, init));
    show('operator');
    fireEvent.click(await screen.findByRole('button', { name: '确认已查看通知 notice-1' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认已查看' }));
    expect(await screen.findByText('查看确认暂时不可用')).toBeInTheDocument();
    expect(screen.getByText('未确认查看')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: '人工查看确认' })).toBeInTheDocument();
  });

  it('can leave a new channel disabled without requiring SMTP credentials', async () => {
    const fetchMock = stubApi({ 'GET /api/v1/alert-notification-config': { ...config, configured: false, enabled: false, host: '', username: '', sender: '', recipients: [], password_configured: false } });
    show('admin');
    await screen.findByLabelText('SMTP 服务器');
    await waitFor(() => expect(screen.getByRole('button', { name: '保存通知配置' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存通知配置' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-notification-config', expect.objectContaining({ method: 'PATCH' })));
    const body = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body;
    expect(JSON.parse(String(body))).toEqual({ enabled: false, port: 587, security: 'starttls', username: '' });
  });

  it('does not offer unlimited retries after the maximum attempt budget', async () => {
    const fetchMock = stubApi({ 'GET /api/v1/alert-notifications': { total: 1, items: [{ ...notification, status: 'failed', attempts: 5 }] } });
    show('admin');
    const retry = await screen.findByRole('button', { name: '重试通知 notice-1' });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/retry'))).toBe(false);
  });

  it('retains failed status and shows the backend retry error', async () => {
    const fetchMock = stubApi({ 'GET /api/v1/alert-notifications': { total: 1, items: [{ ...notification, status: 'failed', sent_at: null }] } });
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => String(input).endsWith('/retry')
      ? response({ detail: '发送次数已达上限' }, 409) : normal(input, init));
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '重试通知 notice-1' }));
    expect(await screen.findByText('发送次数已达上限')).toBeInTheDocument();
    expect(screen.getByText('发送失败')).toBeInTheDocument();
    expect(screen.queryByText('重试已提交')).not.toBeInTheDocument();
  });

  it('shows a delivery history error rather than reporting an empty successful history', async () => {
    const fetchMock = stubApi();
    const normal = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) => String(input).startsWith('/api/v1/alert-notifications?')
      ? response({ detail: '通知记录暂时不可用' }, 503) : normal(input, init));
    show('auditor');
    expect(await screen.findByText('通知记录暂时不可用')).toBeInTheDocument();
    expect(screen.queryByText('暂无通知记录')).not.toBeInTheDocument();
  });

  it('shows a safe title and correlation details for an unmapped alert without an alert page', async () => {
    stubApi({ 'GET /api/v1/alert-notifications': { total: 1, items: [{ ...notification, alert_id: null, kind: 'asset_mapping_failed', title: 'CPU alert on prod-node', source: 'zabbix', external_id: 'external-17', asset_locator: { asset_host: '192.0.2.17' } }] } });
    show('auditor');
    expect(await screen.findByText('CPU alert on prod-node')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看通知详情 notice-1' }));
    expect(await screen.findByText('zabbix')).toBeInTheDocument();
    expect(screen.getByText('external-17')).toBeInTheDocument();
    expect(screen.getByText('192.0.2.17')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看告警' })).not.toBeInTheDocument();
  });
});
