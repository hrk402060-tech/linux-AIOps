import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../api/auth';
import { ApiError } from '../api/client';
import { getIdentityOptions, loginLdap } from '../api/identitySources';
import { AccessGate } from './AccessGate';

const user: AuthUser = {
  id: 'external-user', username: 'operator', display_name: null, role: 'operator',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
vi.mock('../api/identitySources', () => ({ getIdentityOptions: vi.fn(), loginLdap: vi.fn() }));
vi.mock('./LoginPlatformScene', () => ({ LoginPlatformScene: () => <span /> }));
vi.mock('./IdentityMfaGate', () => ({
  IdentityMfaGate: ({ onAuthenticated, onCancel }: {
    onAuthenticated: (next: AuthUser) => void; onCancel: () => void;
  }) => <section aria-label="外部身份二次验证">
    <button onClick={() => onAuthenticated(user)}>测试验证成功</button>
    <button onClick={onCancel}>取消外部验证</button>
  </section>,
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getIdentityOptions).mockResolvedValue({
    local_login_enabled: false, ldap_enabled: true, oidc_enabled: true,
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});
const mount = (onAuthenticated = vi.fn()) => {
  render(<ConfigProvider theme={{ token: { motion: false } }}><AccessGate onAuthenticated={onAuthenticated} /></ConfigProvider>);
  return onAuthenticated;
};

it('uses the configured LDAP login and retains entered credentials on failure', async () => {
  const authenticated = mount();
  await screen.findByRole('link', { name: '通过统一身份登录' });
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: ' operator ' } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: ' password-with-spaces ' } });
  vi.mocked(loginLdap).mockRejectedValueOnce(new Error('LDAP 账号或密码不正确'));
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('LDAP 账号或密码不正确');
  expect(loginLdap).toHaveBeenCalledWith('operator', ' password-with-spaces ', undefined);
  expect(screen.getByLabelText('密码')).toHaveValue(' password-with-spaces ');
  expect(authenticated).not.toHaveBeenCalled();
});

it('requires external MFA enrollment before authenticating a LDAP user', async () => {
  const authenticated = mount();
  await screen.findByRole('link', { name: '通过统一身份登录' });
  vi.mocked(loginLdap).mockRejectedValueOnce(new ApiError('需绑定 MFA', 401, 'mfa_setup_required'));
  fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'operator' } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password-value' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  expect(await screen.findByRole('region', { name: '外部身份二次验证' })).toBeVisible();
  expect(authenticated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '测试验证成功' }));
  expect(authenticated).toHaveBeenCalledWith(user);
});

it.each(['测试验证成功', '取消外部验证'])('clears the OIDC challenge URL after %s', async (action) => {
  window.history.replaceState({}, '', '/?identity_mfa=required&page=assets');
  const authenticated = mount();
  fireEvent.click(screen.getByRole('button', { name: action }));
  await waitFor(() => expect(screen.queryByRole('region', { name: '外部身份二次验证' })).not.toBeInTheDocument());
  expect(new URLSearchParams(window.location.search).has('identity_mfa')).toBe(false);
  expect(new URLSearchParams(window.location.search).get('page')).toBe('assets');
  expect(authenticated).toHaveBeenCalledTimes(action === '测试验证成功' ? 1 : 0);
});

it('shows only unified identity login when local and LDAP login are disabled', async () => {
  vi.mocked(getIdentityOptions).mockResolvedValue({
    local_login_enabled: false, ldap_enabled: false, oidc_enabled: true,
  });
  mount();
  const link = await screen.findByRole('link', { name: '通过统一身份登录' });
  expect(link).toHaveAttribute('href', '/api/v1/auth/identity/oidc/start');
  expect(screen.queryByLabelText('用户名')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '登录' })).not.toBeInTheDocument();
});
