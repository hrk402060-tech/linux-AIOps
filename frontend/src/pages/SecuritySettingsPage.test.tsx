import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { SecuritySettingsPage } from './SecuritySettingsPage';

afterEach(() => vi.unstubAllGlobals());

it('loads login security and AI configuration together in the dedicated settings page', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
    ok: true,
    json: async () => String(input).endsWith('/auth-policy') ? {
      login_failure_threshold: 5, lock_duration_seconds: 900, session_ttl_seconds: 43200,
      require_mfa: false, mfa_enforcement: 'off', allow_user_mfa_reset: true,
      source: '环境变量', updated_by: '环境变量', updated_at: null,
    } : String(input).endsWith('/settings/terminal') ? {
      enabled: false, base_url: '', model: '', timeout_seconds: 30,
      api_key_masked: null, updated_at: null,
    } : [],
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><SecuritySettingsPage /></ConfigProvider>);
  expect(screen.getByRole('heading', { name: '安全设置' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '登录安全策略' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'AI 配置' })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: '保存登录策略' })).toBeEnabled());
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.anything()));
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/providers/config', expect.anything());
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/auth/users'))).toBe(false);
});
