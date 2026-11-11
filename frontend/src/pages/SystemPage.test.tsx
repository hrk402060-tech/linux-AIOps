import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider, Modal } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { SystemPage } from './SystemPage';
import { asset, auditor, systemStatus, terminalAI } from './SystemPage.test-helpers';

vi.mock('../components/AISettingsPanel', () => ({
  AISettingsPanel: () => <section aria-label="AI 配置" />,
}));
vi.mock('../components/AuthPolicyPanel', () => ({
  AuthPolicyPanel: () => <section aria-label="登录安全策略" />,
}));

afterEach(() => { Modal.destroyAll(); vi.unstubAllGlobals(); });

it('shows service status and security boundaries from the read-only endpoint', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    database_status: 'ok', database_engine: 'sqlite', schema_revision: '20260915_21',
    monitoring_interval_seconds: 300, knowledge_sync_interval_seconds: 14400,
    terminal: { max_connections: 4, idle_timeout_seconds: 900, max_duration_seconds: 28800, host_key_verification: true },
    security: { scan_read_only_default: true, ai_direct_execution: false, credential_encryption: 'AES-256-GCM', authentication: 'password_session' },
    checked_at: '2026-09-15T00:00:00Z',
  }) }));
  render(<ConfigProvider><SystemPage currentUser={auditor} /></ConfigProvider>);
  expect(await screen.findByText('核心服务运行正常')).toBeInTheDocument();
  expect(screen.getByText('首次连接自动接受，仅内存使用')).toBeInTheDocument();
  expect(screen.getByText('禁止')).toBeInTheDocument();
  expect(screen.getByText('AES-256-GCM')).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(
    '/api/v1/system/status', expect.objectContaining({ credentials: 'include' }),
  ));
});

it('reports unavailable database without exposing settings', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ database_status: 'unavailable', database_engine: 'postgresql', schema_revision: null, monitoring_interval_seconds: 300, knowledge_sync_interval_seconds: 3600, terminal: { max_connections: 1, idle_timeout_seconds: 60, max_duration_seconds: 600, host_key_verification: false }, security: { scan_read_only_default: true, ai_direct_execution: false, credential_encryption: 'AES-256-GCM', authentication: 'password_session' }, checked_at: '' }) }));
  render(<ConfigProvider><SystemPage currentUser={auditor} /></ConfigProvider>);
  expect(await screen.findByText('平台服务不可用')).toBeInTheDocument();
  expect(screen.queryByText('database_url')).not.toBeInTheDocument();
});

it('shows user management only to administrators', async () => {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => Promise.resolve({
    ok: true,
      json: async () => String(input).endsWith('/settings/terminal') ? terminalAI
        : String(input).endsWith('/providers/config') ? []
          : String(input).endsWith('/auth/users') ? [admin, auditor]
            : String(input).endsWith('/assets') ? [asset]
              : { ...systemStatus, schema_revision: '20260915_21', checked_at: '2026-09-15T00:00:00Z' },
  })));
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  expect(await screen.findByText('用户与权限')).toBeInTheDocument();
  expect(await screen.findByText('audit-user')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '新建用户' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '登录安全策略' })).not.toBeInTheDocument();
});

it('lets an administrator inspect and revoke one active session', async () => {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  const activeSession = {
    id: 'session-1', user_id: auditor.id, username: auditor.username,
    display_name: auditor.display_name, role: auditor.role,
    created_at: '2026-09-15T01:00:00Z', last_used_at: '2026-09-15T02:00:00Z',
    expires_at: '2026-09-16T02:00:00Z', client_ip: '10.0.0.8',
    user_agent: 'Mozilla/5.0 Test Browser', status: 'active', is_current: false,
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/auth/sessions/page?user_id=audit-1')) {
      return Promise.resolve({ ok: true, json: async () => ({
        items: [activeSession], total: 1, offset: 0, limit: 20,
      }) });
    }
    if (url.endsWith('/sessions/session-1/revoke')) {
      return Promise.resolve({ ok: true, status: 204, json: async () => null });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/auth/users') ? [admin, auditor]
        : url.endsWith('/assets') ? [asset] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);

  fireEvent.click(await screen.findByLabelText('查看 audit-user 活动会话'));
  const sessionDialog = within(await screen.findByRole('dialog'));
  expect(await sessionDialog.findByText('Mozilla/5.0 Test Browser')).toBeInTheDocument();
  fireEvent.click(sessionDialog.getAllByRole('button', { name: '撤销会话' })[0]);
  const confirmDialog = within((await screen.findAllByRole('dialog')).at(-1)!);
  expect(confirmDialog.getByText('撤销此活动会话？')).toBeInTheDocument();
  fireEvent.click(confirmDialog.getByRole('button', { name: '确认撤销' }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/audit-1/sessions/session-1/revoke',
    expect.objectContaining({ method: 'POST' }),
  ));
});

it('shows a locked user and lets an administrator unlock the account', async () => {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  const lockedUser = {
    ...auditor,
    id: 'locked-1',
    username: 'locked-user',
    locked_until: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
    failed_login_attempts: 0,
  };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/auth/users/locked-1/unlock')) {
      expect(init?.method).toBe('POST');
      return Promise.resolve({ ok: true, json: async () => ({ ...lockedUser, locked_until: null }) });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/auth/users') ? [admin, lockedUser]
        : url.endsWith('/assets') ? [asset]
          : { ...systemStatus, schema_revision: '20261024_44', checked_at: '2026-10-30T00:00:00Z' },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  expect(await screen.findByText('登录锁定')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('解锁 locked-user'));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/locked-1/unlock',
    expect.objectContaining({ method: 'POST' }),
  ));
});

it('returns to login after the current administrator session is revoked', async () => {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  const onLogout = vi.fn();
  const currentSession = {
    id: 'session-current', user_id: admin.id, username: admin.username,
    display_name: admin.display_name, role: admin.role,
    created_at: '2026-09-15T01:00:00Z', last_used_at: '2026-09-15T02:00:00Z',
    expires_at: '2026-09-16T02:00:00Z', client_ip: '10.0.0.8',
    user_agent: 'Mozilla/5.0 Admin Browser', status: 'active', is_current: true,
  };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/auth/sessions/page?user_id=admin-1')) {
      return Promise.resolve({ ok: true, json: async () => ({
        items: [currentSession], total: 1, offset: 0, limit: 20,
      }) });
    }
    if (url.endsWith('/sessions/session-current/revoke')) {
      return Promise.resolve({ ok: true, status: 204, json: async () => null });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/auth/users') ? [admin]
        : url.endsWith('/assets') ? [asset] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} onLogout={onLogout} /></ConfigProvider>);
  fireEvent.click(await screen.findByLabelText('查看 security-admin 活动会话'));
  const sessionDialog = within(await screen.findByRole('dialog'));
  expect(await sessionDialog.findByText('当前会话')).toBeInTheDocument();
  fireEvent.click(sessionDialog.getAllByRole('button', { name: '撤销会话' })[0]);
  const confirmDialog = within((await screen.findAllByRole('dialog')).at(-1)!);
  fireEvent.click(confirmDialog.getByRole('button', { name: '确认撤销' }));
  await waitFor(() => expect(onLogout).toHaveBeenCalledOnce());
});
it.each(['operator', 'auditor'] as const)('does not expose or request AI configuration to %s', async (role) => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ database_status: 'unavailable', database_engine: 'postgresql', schema_revision: null, monitoring_interval_seconds: 300, knowledge_sync_interval_seconds: 3600, terminal: { max_connections: 1, idle_timeout_seconds: 60, max_duration_seconds: 600, host_key_verification: false }, security: { scan_read_only_default: true, ai_direct_execution: false, credential_encryption: 'AES-256-GCM', authentication: 'password_session' }, checked_at: '' }) });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><SystemPage currentUser={{ ...auditor, role }} /></ConfigProvider>);
  expect(await screen.findByText('平台服务不可用')).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '登录安全策略' })).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalled();
  expect(fetchMock.mock.calls.every(([input]) => String(input) === '/api/v1/system/status')).toBe(true);
});

function renderPasswordReset() {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/auth/users/audit-1/reset-password')) {
      return Promise.resolve({ ok: true, json: async () => auditor });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/settings/terminal') ? terminalAI
        : url.endsWith('/providers/config') ? []
          : url.endsWith('/auth/users') ? [admin, auditor]
            : url.endsWith('/assets') ? [asset] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  return fetchMock;
}

it('opens a confirmation instead of immediately revoking all user sessions', async () => {
  const fetchMock = renderPasswordReset();
  await screen.findByText('audit-user');
  const row = screen.getByLabelText('查看 audit-user 活动会话').closest('tr')!;
  fireEvent.click(within(row).getByRole('button', { name: '撤销全部会话' }));
  expect(await screen.findByText('撤销全部会话：audit-user')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/revoke-sessions'))).toBe(false);
});

it('clears forced password change after cancel and resets normally', async () => {
  const fetchMock = renderPasswordReset();
  fireEvent.click(await screen.findByLabelText('重置 audit-user 密码'));
  const dialog = within(await screen.findByRole('dialog')); 
  const forceChange = dialog.getByRole('checkbox', { name: '要求用户下次登录修改密码' });
  expect(forceChange).not.toBeChecked();
  fireEvent.click(forceChange);
  fireEvent.click(dialog.getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByLabelText('重置 audit-user 密码'));
  const reopened = within(await screen.findByRole('dialog'));
  expect(reopened.getByRole('checkbox', { name: '要求用户下次登录修改密码' })).not.toBeChecked();
  fireEvent.change(reopened.getByLabelText('新密码'), { target: { value: 'Reset123!' } });
  fireEvent.click(reopened.getByRole('button', { name: '确认重置' }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/audit-1/reset-password',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ password: 'Reset123!', must_change_password: false }),
    }),
  ));
}, 30000);

it('requires a password change when the administrator selects it', async () => {
  const fetchMock = renderPasswordReset();
  fireEvent.click(await screen.findByLabelText('重置 audit-user 密码'));
  const dialog = within(await screen.findByRole('dialog'));
  fireEvent.click(dialog.getByRole('checkbox', { name: '要求用户下次登录修改密码' }));
  fireEvent.change(dialog.getByLabelText('新密码'), { target: { value: 'Forced123!' } });
  fireEvent.click(dialog.getByRole('button', { name: '确认重置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/users/audit-1/reset-password',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ password: 'Forced123!', must_change_password: true }),
    }),
  ));
}, 30000);

it('lets an administrator assign scoped asset view and SSH permissions', async () => {
  const admin = { ...auditor, id: 'admin-1', username: 'security-admin', display_name: '安全管理员', role: 'admin' as const };
  const operator = { ...auditor, id: 'operator-1', username: 'ops-user', display_name: '运维用户', role: 'operator' as const };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/asset-access') && init?.method === 'PUT') {
      return Promise.resolve({ ok: true, json: async () => ({ user_id: operator.id, asset_scope: 'assigned', revision: 1, entries: [{ asset_id: 'asset-1', can_ssh: true }] }) });
    }
    if (url.endsWith('/asset-access')) {
      return Promise.resolve({ ok: true, json: async () => ({ user_id: operator.id, asset_scope: 'assigned', revision: 0, entries: [] }) });
    }
    return Promise.resolve({
      ok: true,
      json: async () => url.endsWith('/settings/terminal') ? terminalAI
        : url.endsWith('/providers/config') ? []
          : url.endsWith('/auth/users') ? [admin, operator]
            : url.endsWith('/assets') ? [asset] : systemStatus,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '资产授权' }));
  const dialog = within(await screen.findByRole('dialog'));
  expect(await dialog.findByText('核心服务器')).toBeInTheDocument();
  fireEvent.click(dialog.getByRole('checkbox', { name: '核心服务器查看权限' }));
  fireEvent.click(dialog.getByRole('checkbox', { name: '核心服务器SSH权限' }));
  fireEvent.change(dialog.getByLabelText(/输入用户名确认/), { target: { value: 'ops-user' } });
  fireEvent.change(dialog.getByLabelText('变更原因'), { target: { value: '资产职责调整' } });
  fireEvent.click(dialog.getByRole('button', { name: '保存资产授权' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/auth/users/${operator.id}/asset-access`,
    expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({
        asset_scope: 'assigned', entries: [{ asset_id: 'asset-1', can_ssh: true }],
        expected_revision: 0, reason: '资产职责调整', confirmation: 'ops-user',
      }),
    }),
  ));
}, 30000);
