import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { IdentitySettingsPanel } from './IdentitySettingsPanel';
import { getIdentitySource, saveIdentitySource, testIdentitySource } from '../../api/identitySources';
vi.mock('../../api/identitySources', () => ({
  getIdentitySource: vi.fn(), saveIdentitySource: vi.fn(), testIdentitySource: vi.fn(),
}));
const ldap = {
  server_url: 'ldaps://directory.example:636', base_dn: 'dc=example', bind_dn: 'cn=reader,dc=example',
  user_filter: '(uid={username})', username_attribute: 'uid', subject_attribute: 'entryUUID',
  display_name_attribute: 'displayName', timeout_seconds: 10, secret_configured: true,
  tested_at: null, test_success: false, verified_admin_login: false, updated_at: null,
};
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getIdentitySource).mockResolvedValue(ldap); });
const mount = () => render(<ConfigProvider theme={{ token: { motion: false } }}><IdentitySettingsPanel provider="ldap" /></ConfigProvider>);
it('tests current unsaved connection values and displays real failure without saving', async () => {
  mount();
  const url = await screen.findByLabelText('LDAP 地址');
  await waitFor(() => expect(url).toHaveValue(ldap.server_url));
  fireEvent.change(url, { target: { value: 'ldaps://new-directory.example:636' } });
  vi.mocked(testIdentitySource).mockResolvedValue({ success: false, message: 'TLS 证书验证失败', checked_at: '2026-11-17T00:00:00Z', latency_ms: 123 });
  fireEvent.click(screen.getByRole('button', { name: '测试当前配置' }));
  expect(await screen.findByText('TLS 证书验证失败')).toBeInTheDocument();
  expect(testIdentitySource).toHaveBeenCalledWith('ldap', expect.objectContaining({ server_url: 'ldaps://new-directory.example:636' }));
  expect(saveIdentitySource).not.toHaveBeenCalled();
  expect(screen.getByLabelText('绑定密码')).toHaveValue('');
});
it('preserves replacement secret after a failed save', async () => {
  mount(); await waitFor(() => expect(screen.getByLabelText('LDAP 地址')).toHaveValue(ldap.server_url));
  fireEvent.change(screen.getByLabelText('绑定密码'), { target: { value: 'pending-secret' } });
  vi.mocked(saveIdentitySource).mockRejectedValue(new Error('身份源保存失败'));
  fireEvent.click(screen.getByRole('button', { name: '保存连接' }));
  expect(await screen.findByText('身份源保存失败')).toBeInTheDocument();
  expect(screen.getByLabelText('绑定密码')).toHaveValue('pending-secret');
});
