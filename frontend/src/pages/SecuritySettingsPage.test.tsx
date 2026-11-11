import { fireEvent, render, screen, within } from '@testing-library/react';
import { ConfigProvider, Modal } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../api/auth';
import { AuthPolicyPanel } from '../components/AuthPolicyPanel';
import { AISettingsPanel } from '../components/AISettingsPanel';
import { SecuritySettingsPage } from './SecuritySettingsPage';

vi.mock('../components/AuthPolicyPanel', () => ({
  AuthPolicyPanel: vi.fn(() => <section aria-label="登录安全策略" />),
}));
vi.mock('../components/AISettingsPanel', () => ({
  AISettingsPanel: vi.fn(() => <section aria-label="AI 配置" />),
}));

const admin: AuthUser = {
  id: 'user-admin', username: 'admin', display_name: '管理员', role: 'admin',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
const selfStatus = { enabled: false, enforcement: 'off', required: false, can_disable: true };
afterEach(() => { Modal.destroyAll(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it('composes personal MFA and administrator panels in one settings page', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => selfStatus });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SecuritySettingsPage currentUser={admin} onLogout={vi.fn()} /></ConfigProvider>);
  expect(screen.getByRole('heading', { name: '安全设置', hidden: true })).toBeInTheDocument();
  expect(screen.getAllByRole('heading', { level: 1, hidden: true })).toHaveLength(1);
  expect(await screen.findByRole('button', { name: '启用 MFA', hidden: true })).toBeEnabled();
  expect(screen.getByRole('region', { name: '登录安全策略', hidden: true })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'AI 配置', hidden: true })).toBeInTheDocument();
  expect(AuthPolicyPanel).toHaveBeenCalled();
  expect(AISettingsPanel).toHaveBeenCalled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me/mfa', expect.anything());
});

it.each(['operator', 'auditor'] as const)('loads only personal MFA for %s without administrator requests', async (role) => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => selfStatus });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SecuritySettingsPage currentUser={{ ...admin, role }} onLogout={vi.fn()} /></ConfigProvider>);
  expect(await screen.findByRole('button', { name: '启用 MFA' })).toBeEnabled();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.queryByRole('region', { name: '登录安全策略' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  expect(AuthPolicyPanel).not.toHaveBeenCalled();
  expect(AISettingsPanel).not.toHaveBeenCalled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me/mfa', expect.anything());
}, 15_000);

it('preserves QR enrollment and one-time recovery codes within unified security settings', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => Promise.resolve({
    ok: true,
    json: async () => String(input).endsWith('/setup') ? {
      enabled: false, secret: 'SETUPSECRET', otpauth_uri: 'otpauth://totp/Linux:user?secret=SETUPSECRET',
    } : String(input).endsWith('/confirm') ? { enabled: true, recovery_codes: ['RECOVERY-ONCE'] } : selfStatus,
  }));
  const onLogout = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SecuritySettingsPage currentUser={{ ...admin, role: 'operator' }} onLogout={onLogout} /></ConfigProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '启用 MFA' }));
  const setup = within(await screen.findByRole('dialog', { name: '扫码绑定 MFA' }));
  expect(setup.getByLabelText('MFA 二维码')).toBeInTheDocument();
  fireEvent.change(setup.getByLabelText('验证器验证码'), { target: { value: '123456' } });
  fireEvent.click(setup.getByRole('button', { name: '确认启用' }));
  const recovery = within(await screen.findByRole('dialog', { name: 'MFA 已启用' }));
  expect(recovery.getByText('RECOVERY-ONCE')).toBeInTheDocument();
  expect(onLogout).not.toHaveBeenCalled();
  fireEvent.click(recovery.getByRole('button', { name: '保存恢复码并重新登录' }));
  expect(onLogout).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    '/api/v1/auth/me/mfa', '/api/v1/auth/me/mfa/setup', '/api/v1/auth/me/mfa/confirm',
  ]);
}, 15_000);
