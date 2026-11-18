import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../api/auth';
import {
  confirmIdentityMfa, getIdentityMfaStatus, setupIdentityMfa, verifyIdentityMfa,
} from '../api/identitySources';
import { IdentityMfaGate } from './IdentityMfaGate';

vi.mock('../api/identitySources', () => ({
  confirmIdentityMfa: vi.fn(), getIdentityMfaStatus: vi.fn(),
  setupIdentityMfa: vi.fn(), verifyIdentityMfa: vi.fn(),
}));
const user: AuthUser = {
  id: 'external-user', username: 'operator', display_name: null, role: 'operator',
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getIdentityMfaStatus).mockResolvedValue({ required: true, enrollment_required: false });
  vi.mocked(verifyIdentityMfa).mockResolvedValue(user);
});
const mount = (authenticated = vi.fn()) => {
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <IdentityMfaGate onAuthenticated={authenticated} onCancel={vi.fn()} />
  </ConfigProvider>);
  return authenticated;
};
it('verifies an external login recovery code before issuing the frontend session', async () => {
  const authenticated = mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '验证并登录' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('验证码或恢复码'), { target: { value: 'RECOVERY-CODE' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并登录' }));
  await waitFor(() => expect(authenticated).toHaveBeenCalledWith(user));
  expect(verifyIdentityMfa).toHaveBeenCalledWith('RECOVERY-CODE');
  expect(confirmIdentityMfa).not.toHaveBeenCalled();
});
it('enrolls with a QR code and displays recovery codes before entering the platform', async () => {
  vi.mocked(getIdentityMfaStatus).mockResolvedValue({ required: true, enrollment_required: true });
  vi.mocked(setupIdentityMfa).mockResolvedValue({
    enabled: false, secret: 'SETUPVALUE', otpauth_uri: 'otpauth://totp/Linux:user?secret=SETUPVALUE',
  });
  vi.mocked(confirmIdentityMfa).mockResolvedValue({ user, recovery_codes: ['ONCE-ONLY-CODE'] });
  const authenticated = mount();
  expect(await screen.findByLabelText('MFA 二维码')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('验证器验证码'), { target: { value: '123456' } });
  fireEvent.click(screen.getByRole('button', { name: '确认绑定' }));
  expect(await screen.findByText('ONCE-ONLY-CODE')).toBeInTheDocument();
  expect(confirmIdentityMfa).toHaveBeenCalledWith('123456');
  expect(authenticated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '已保存，进入平台' }));
  expect(authenticated).toHaveBeenCalledWith(user);
});
it('keeps an incorrect MFA code for retry and does not authenticate', async () => {
  vi.mocked(verifyIdentityMfa).mockRejectedValueOnce(new Error('验证码错误或已过期'));
  const authenticated = mount();
  await waitFor(() => expect(screen.getByRole('button', { name: '验证并登录' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('验证码或恢复码'), { target: { value: '000000' } });
  fireEvent.click(screen.getByRole('button', { name: '验证并登录' }));
  expect(await screen.findByText('验证码错误或已过期')).toBeInTheDocument();
  expect(screen.getByLabelText('验证码或恢复码')).toHaveValue('000000');
  expect(authenticated).not.toHaveBeenCalled();
});
it('blocks expired external authentication challenges', async () => {
  vi.mocked(getIdentityMfaStatus).mockRejectedValueOnce(new Error('身份验证流程已过期'));
  const authenticated = mount();
  expect(await screen.findByText('身份验证流程已过期')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '验证并登录' })).toBeDisabled();
  expect(authenticated).not.toHaveBeenCalled();
  expect(setupIdentityMfa).not.toHaveBeenCalled();
});
