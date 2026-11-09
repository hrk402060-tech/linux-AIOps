import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider, Modal } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { SystemPage } from './SystemPage';
import { auditor, systemStatus } from './SystemPage.test-helpers';

vi.mock('../components/AISettingsPanel', () => ({
  AISettingsPanel: () => <section aria-label="AI 配置" />,
}));
vi.mock('../components/AuthPolicyPanel', () => ({
  AuthPolicyPanel: () => <section aria-label="登录安全策略" />,
}));
afterEach(() => { Modal.destroyAll(); vi.unstubAllGlobals(); });

const admin = {
  ...auditor, id: 'admin-1', username: 'security-admin',
  display_name: '安全管理员', role: 'admin' as const, mfa_enabled: true,
};

it('lets an administrator configure MFA and shows one-time recovery codes', async () => {
  const target = { ...auditor, id: 'mfa-1', username: 'mfa-user', mfa_enabled: false };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/auth/users/mfa-1/mfa/setup')) {
      expect(init?.method).toBe('POST');
      return Promise.resolve({ ok: true, json: async () => ({
        enabled: false, secret: 'JBSWY3DPEHPK3PXP',
        otpauth_uri: 'otpauth://totp/Linux:mfa-user?secret=JBSWY3DPEHPK3PXP',
      }) });
    }
    if (url.endsWith('/auth/users/mfa-1/mfa/confirm')) {
      expect(init?.method).toBe('POST');
      return Promise.resolve({ ok: true, json: async () => ({
        enabled: true, recovery_codes: ['AAAAA-BBBBB', 'CCCCC-DDDDD'],
      }) });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/auth/users') ? [admin, target]
        : url.endsWith('/assets') ? [] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  fireEvent.click(await screen.findByLabelText('设置 mfa-user MFA'));
  const mfaDialog = within(await screen.findByRole('dialog'));
  expect(mfaDialog.getByLabelText('MFA 二维码')).toBeInTheDocument();
  expect(mfaDialog.getByText('无法扫码时手动绑定')).toBeInTheDocument();
  expect(await mfaDialog.findByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument();
  fireEvent.change(mfaDialog.getByLabelText('验证器验证码'), { target: { value: '123456' } });
  fireEvent.click(mfaDialog.getByRole('button', { name: '确认启用' }));
  expect(await mfaDialog.findByText('恢复码只显示这一次，请由用户安全保存。')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/users/mfa-1/mfa/confirm', expect.objectContaining({ method: 'POST' }));
});

it('requires confirmation and returns to login when the current administrator resets MFA', async () => {
  const onLogout = vi.fn();
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/mfa/reset') ? { enabled: false }
        : url.endsWith('/auth/users') ? [admin] : url.endsWith('/assets') ? [] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} onLogout={onLogout} /></ConfigProvider>);
  fireEvent.click(await screen.findByLabelText('重置 security-admin MFA'));
  const confirmation = within(await screen.findByRole('dialog'));
  expect(await confirmation.findByText(/原恢复码、登录会话和 SSH 连接立即失效/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/mfa/reset'))).toBe(false);
  fireEvent.click(confirmation.getByRole('button', { name: '确认重置' }));
  await waitFor(() => expect(onLogout).toHaveBeenCalledOnce());
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/admin-1/mfa/reset', expect.objectContaining({ method: 'POST' }),
  );
});

it('shows the last MFA administrator reset rejection without logging out or changing MFA', async () => {
  const detail = '强制 MFA 已开启，不能重置最后一名已绑定 MFA 的启用管理员；请先关闭强制 MFA，或让另一名管理员绑定 MFA';
  const onLogout = vi.fn();
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/mfa/reset')) {
      return Promise.resolve({ ok: false, status: 409, json: async () => ({ detail }) });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/auth/users') ? [admin] : url.endsWith('/assets') ? [] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} onLogout={onLogout} /></ConfigProvider>);
  fireEvent.click(await screen.findByLabelText('重置 security-admin MFA'));
  const confirmation = within(await screen.findByRole('dialog'));
  fireEvent.click(confirmation.getByRole('button', { name: '确认重置' }));

  expect(await screen.findByText(detail)).toBeInTheDocument();
  expect(onLogout).not.toHaveBeenCalled();
  expect(screen.getByText('MFA 已启用')).toBeInTheDocument();
  expect(screen.queryByLabelText('设置 security-admin MFA')).not.toBeInTheDocument();
  expect(screen.queryByText('MFA 已重置')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/auth/users'))).toHaveLength(1);
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/admin-1/mfa/reset', expect.objectContaining({ method: 'POST' }),
  );
});
