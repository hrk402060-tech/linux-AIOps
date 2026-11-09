import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';

import { AuthPolicyPanel } from './AuthPolicyPanel';

const policy = {
  login_failure_threshold: 5,
  lock_duration_seconds: 900,
  session_ttl_seconds: 43200,
  require_mfa: false,
  source: '平台配置',
  updated_by: 'admin',
  updated_at: '2026-10-31T10:00:00Z',
};

afterEach(() => vi.unstubAllGlobals());

it('loads and saves the administrator login security policy', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve({
    ok: true,
    json: async () => String(input).endsWith('/auth-policy') && init?.method === 'PUT' ? {
      ...policy, login_failure_threshold: 7,
    } : policy,
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuthPolicyPanel /></ConfigProvider>);

  expect(await screen.findByText('登录安全策略')).toBeInTheDocument();
  const threshold = await screen.findByRole('spinbutton', { name: '连续失败次数' });
  await waitFor(() => expect(threshold).toHaveValue('5'));
  expect(screen.getByText(/新策略仅适用于后续登录/)).toBeInTheDocument();
  fireEvent.change(threshold, { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: '保存登录策略' }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/system/auth-policy',
    expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({
        login_failure_threshold: 7,
        lock_duration_seconds: 900,
        session_ttl_seconds: 43200,
        require_mfa: false,
      }),
    }),
  ));
});

it('shows the server validation error when forced MFA cannot be enabled', async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve({
    ok: init?.method === 'PUT' ? false : true,
    json: async () => init?.method === 'PUT' ? { detail: '仍有 1 个启用用户未绑定 MFA，不能启用强制 MFA' } : policy,
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuthPolicyPanel /></ConfigProvider>);

  await waitFor(() => expect(screen.getByRole('button', { name: '保存登录策略' })).toBeEnabled());
  fireEvent.click(screen.getByRole('switch', { name: '强制所有启用用户使用 MFA' }));
  fireEvent.click(screen.getByRole('button', { name: '保存登录策略' }));

  expect(await screen.findByText('仍有 1 个启用用户未绑定 MFA，不能启用强制 MFA')).toBeInTheDocument();
  const request = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
  expect(request).toBeDefined();
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({
    login_failure_threshold: 5, lock_duration_seconds: 900,
    session_ttl_seconds: 43200, require_mfa: true,
  });
});

it('keeps loading and failed policy values empty and blocks saving until retry succeeds', async () => {
  let finish: ((response: Response) => void) | undefined;
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
    .mockResolvedValue({ ok: true, json: async () => policy });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuthPolicyPanel /></ConfigProvider>);
  expect(screen.getByRole('spinbutton', { name: '连续失败次数' })).toHaveValue('');
  expect(screen.getByRole('button', { name: '保存登录策略' })).toBeDisabled();

  await act(async () => finish?.({
    ok: false, status: 503, json: async () => ({ detail: '登录策略暂时不可用' }),
  } as Response));
  expect(await screen.findByText('登录策略暂时不可用')).toBeInTheDocument();
  expect(screen.queryByText('使用环境默认值')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存登录策略' })).toBeDisabled();

  fireEvent.click(screen.getByRole('button', { name: '重新加载登录安全策略' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存登录策略' })).toBeEnabled());
  expect(screen.getByRole('spinbutton', { name: '连续失败次数' })).toHaveValue('5');
});

it('rejects an invalid response instead of presenting made-up policy values', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ database_status: 'ok' }) }));
  render(<ConfigProvider><AuthPolicyPanel /></ConfigProvider>);
  expect(await screen.findByText('登录安全策略响应无效，请重新加载')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存登录策略' })).toBeDisabled();
});

it('submits the policy only once while a save is still in progress', async () => {
  let finish: ((response: Response) => void) | undefined;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => init?.method === 'PUT'
    ? new Promise<Response>((resolve) => { finish = resolve; })
    : Promise.resolve({ ok: true, json: async () => policy }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuthPolicyPanel /></ConfigProvider>);
  await waitFor(() => expect(screen.getByRole('button', { name: '保存登录策略' })).toBeEnabled());
  const button = screen.getByRole('button', { name: '保存登录策略' });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1));
  await act(async () => finish?.({ ok: true, json: async () => policy } as Response));
});
