import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { getUsers } from '../../api/auth';
import { deleteIdentityBinding, getIdentityBindings, inviteIdentityUser } from '../../api/identitySources';
import { IdentityBindingsPanel } from './IdentityBindingsPanel';

vi.mock('../../api/auth', () => ({ getUsers: vi.fn() }));
vi.mock('../../api/identitySources', () => ({
  getIdentityBindings: vi.fn(), createIdentityBinding: vi.fn(), deleteIdentityBinding: vi.fn(), inviteIdentityUser: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getUsers).mockResolvedValue([{
    id: 'bound-user', username: 'bound-admin', display_name: null, role: 'admin',
    enabled: true, must_change_password: false, created_at: '', last_login_at: null,
  }]);
  vi.mocked(getIdentityBindings).mockResolvedValue([{ subject: 'external-subject', user_id: 'bound-user' }]);
});
const mount = () => render(<ConfigProvider theme={{ token: { motion: false } }}><IdentityBindingsPanel /></ConfigProvider>);

it('removes a binding only after confirmation', async () => {
  vi.mocked(deleteIdentityBinding).mockResolvedValue(undefined);
  mount();
  await screen.findByText('external-subject');
  fireEvent.click(screen.getByRole('button', { name: '解除绑定' }));
  expect(deleteIdentityBinding).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole('button', { name: /确\s*定/ }));
  await waitFor(() => expect(deleteIdentityBinding).toHaveBeenCalledWith('ldap', 'external-subject'));
  await waitFor(() => expect(screen.queryByText('external-subject')).not.toBeInTheDocument());
});
it('retains the last external admin binding when the backend rejects removal', async () => {
  vi.mocked(deleteIdentityBinding).mockRejectedValueOnce(new Error('不能解除最后一个外部管理员身份'));
  mount();
  await screen.findByText('external-subject');
  fireEvent.click(screen.getByRole('button', { name: '解除绑定' }));
  fireEvent.click(await screen.findByRole('button', { name: /确\s*定/ }));
  expect(await screen.findByText('不能解除最后一个外部管理员身份')).toBeInTheDocument();
  expect(screen.getByText('external-subject')).toBeInTheDocument();
});
it('creates a scoped external invitation without requesting a password', async () => {
  vi.mocked(inviteIdentityUser).mockResolvedValueOnce({
    subject: 'invited-subject', username: 'new-reader', user_id: null,
    kind: 'invited', role: 'auditor', expires_at: '2026-11-19T00:00:00Z',
  });
  mount();
  await screen.findByText('external-subject');
  fireEvent.click(screen.getByRole('button', { name: '邀请外部账号' }));
  const dialog = await screen.findByRole('dialog', { name: '邀请外部账号' });
  fireEvent.change(within(dialog).getByLabelText('外部唯一身份'), { target: { value: 'invited-subject' } });
  fireEvent.change(within(dialog).getByLabelText('平台用户名'), { target: { value: 'new-reader' } });
  expect(dialog.querySelector('input[type=password]')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '创建邀请' }));
  await waitFor(() => expect(inviteIdentityUser).toHaveBeenCalledWith('ldap', expect.objectContaining({
    subject: 'invited-subject', username: 'new-reader', role: 'auditor', expires_hours: 24,
  })));
  expect(await screen.findByText('待接入')).toBeInTheDocument();
});
