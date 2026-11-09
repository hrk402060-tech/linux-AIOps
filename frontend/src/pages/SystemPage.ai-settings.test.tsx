import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../api/auth';
import { SystemPage } from './SystemPage';

const admin: AuthUser = { id: 'admin-1', username: 'admin-user', display_name: '管理员', role: 'admin', enabled: true, must_change_password: false, created_at: '', last_login_at: null };
const status = { database_status: 'ok', database_engine: 'sqlite', schema_revision: '20261013_32', monitoring_interval_seconds: 300, knowledge_sync_interval_seconds: 14400, terminal: { max_connections: 4, idle_timeout_seconds: 900, max_duration_seconds: 14400, host_key_verification: true }, security: { scan_read_only_default: true, ai_direct_execution: false, credential_encryption: 'AES-256-GCM', authentication: 'password_session' }, checked_at: '' };
const authPolicy = { login_failure_threshold: 5, lock_duration_seconds: 900, session_ttl_seconds: 43200, require_mfa: false, source: '环境变量', updated_by: '环境变量', updated_at: null };
afterEach(() => vi.unstubAllGlobals());
function stubApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({ ok: true, json: async () => String(input).endsWith('/auth-policy') ? authPolicy : String(input).endsWith('/settings/terminal') ? { enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key_masked: null, updated_at: null } : String(input).endsWith('/providers/config') || String(input).endsWith('/auth/users') ? [] : status }));
  vi.stubGlobal('fetch', fetchMock); return fetchMock;
}
it('loads both configuration sections for administrators and unmounts them after a role change', async () => {
  const fetchMock = stubApi();
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}><SystemPage currentUser={admin} /></ConfigProvider>);
  expect(await screen.findByRole('region', { name: 'AI 配置' })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.anything()));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/providers/config', expect.anything()));
  expect(screen.getByRole('region', { name: '登录安全策略' })).toBeInTheDocument();
  view.rerender(<ConfigProvider><SystemPage currentUser={{ ...admin, role: 'operator' }} /></ConfigProvider>);
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '保存运维配置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '登录安全策略' })).not.toBeInTheDocument();
}, 15_000);
it.each(['operator', 'auditor'] as const)('does not load settings or keys for %s', async (role) => {
  const fetchMock = stubApi();
  render(<ConfigProvider><SystemPage currentUser={{ ...admin, role }} /></ConfigProvider>);
  await screen.findByText('核心服务运行正常');
  expect(screen.queryByRole('region', { name: 'AI 配置' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '登录安全策略' })).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/system/status');
});
