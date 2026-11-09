import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CredentialsPage } from './CredentialsPage';

const credential = {
  id: 'credential-1',
  name: '生产环境只读账号',
  credential_type: 'password' as const,
  username: 'ops-reader',
  secret_mask: '********',
  key_version: 'v1',
  created_at: '2026-09-02T08:00:00Z',
  updated_at: '2026-09-02T08:00:00Z',
  last_used_at: null,
  asset_count: 0,
};

describe('CredentialsPage', () => {
  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [credential] }));
  });

  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('shows masked credentials returned by the API', async () => {
    render(<ConfigProvider><CredentialsPage /></ConfigProvider>);

    expect(await screen.findByText('生产环境只读账号')).toBeInTheDocument();
    expect(screen.getByText('********')).toBeInTheDocument();
    expect(screen.queryByText('never-return-this-secret')).not.toBeInTheDocument();
  });

  it('opens the create credential drawer', async () => {
    render(<ConfigProvider><CredentialsPage /></ConfigProvider>);
    await screen.findByText('生产环境只读账号');

    await userEvent.click(screen.getByRole('button', { name: '新建凭据' }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByLabelText('凭据名称')).toBeInTheDocument();
    expect(within(drawer).getByLabelText('登录用户')).toBeInTheDocument();
  });

  it('submits a new credential without rendering its secret', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => [] })
      .mockResolvedValueOnce({ ok: true, status: 201, json: async () => credential })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => [credential] });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><CredentialsPage /></ConfigProvider>);
    await screen.findByText('暂无凭据');

    await userEvent.click(screen.getAllByRole('button', { name: '新建凭据' })[0]);
    const drawer = await screen.findByRole('dialog');
    await userEvent.type(within(drawer).getByLabelText('凭据名称'), '生产只读凭据');
    await userEvent.type(within(drawer).getByLabelText('登录用户'), 'ops-reader');
    await userEvent.type(within(drawer).getByLabelText('登录密码'), 'secret-value');
    await userEvent.click(within(drawer).getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1][1]?.body).toContain('secret-value');
    expect(screen.queryByText('secret-value')).not.toBeInTheDocument();
  });
});
