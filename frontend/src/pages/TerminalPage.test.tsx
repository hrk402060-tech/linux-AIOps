import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TerminalPage } from './TerminalPage';
import type { TerminalConnectionDetails } from '../types/terminal';

vi.mock('../components/TerminalConsole', () => ({
  TerminalConsole: ({ asset, onStateChange }: { asset: { name: string }; onStateChange: (state: string, details?: TerminalConnectionDetails) => void }) =>
    <div aria-label={asset.name + ' SSH 终端'}>
      <button onClick={() => onStateChange('connected')}>模拟连接成功</button>
      <button onClick={() => onStateChange('closed', { reason: 'terminated', message: '会话已由用户断开', ended_at: '2026-11-03T09:00:00Z' })}>模拟断开完成</button>
      <button onClick={() => onStateChange('failed', { reason: 'transport_error', message: '终端连接已中断，请重新连接' })}>模拟连接异常</button>
    </div>,
}));

const asset = {
  id: 'asset-1', name: 'edge-terminal-01', host: '10.0.0.8', ssh_port: 22,
  os_family: 'openeuler', environment: 'production', group_name: '核心业务',
  credential_id: 'credential-1', credential: { id: 'credential-1', name: 'ops', username: 'admin', credential_type: 'password' },
  labels: [], status: 'online', latency_ms: 5, check_message: 'SSH 端口可达',
  last_checked_at: '2026-09-08T08:00:00Z', created_at: '2026-09-08T08:00:00Z', updated_at: '2026-09-08T08:00:00Z',
};
const response = (body: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
} as Response);

describe('TerminalPage', () => {
  let disconnectResponse: Response | Promise<Response>;

  it('shows only the server catalog at the ordinary SSH entry', async () => {
    render(<ConfigProvider><TerminalPage /></ConfigProvider>);
    expect(await screen.findByRole('table', { name: 'SSH 服务器资源' })).toBeInTheDocument();
    expect(screen.getByRole('tree', { name: '服务器分组' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '新标签连接 edge-terminal-01' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText('10.0.0.8:22')).toBeInTheDocument();
    expect(screen.queryByText('AI 运维助手')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '连接' })).not.toBeInTheDocument();
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it('opens a server inline as a fallback without automatically connecting', async () => {
    render(<TerminalPage />);
    await userEvent.click(await screen.findByRole('button', { name: '当前页打开 edge-terminal-01' }));
    expect(await screen.findByRole('button', { name: '连接' })).toBeEnabled();
    expect(screen.getByText('AI 运维助手')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'SSH 服务器资源' })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/tickets'))).toBe(false);
  }, 15_000);

  it('shows an asset request error and allows refreshing the catalog', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ detail: '资产加载失败' }, 503));
    render(<TerminalPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('资产加载失败');
    await userEvent.click(screen.getByRole('button', { name: '刷新服务器资源' }));
    expect(await screen.findByText('10.0.0.8:22')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  }, 15_000);

  it('never connects an unknown asset or replaces it with another server', async () => {
    render(<TerminalPage initialAssetId="not-visible" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('服务器不存在或当前账号无权访问');
    expect(screen.getByRole('button', { name: '连接' })).toBeDisabled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/tickets'))).toBe(false);
  });

  it('selects an incoming asset without requesting a ticket or connecting', async () => {
    render(<ConfigProvider><TerminalPage initialAssetId="asset-1" /></ConfigProvider>);
    await waitFor(() => expect(screen.getByRole('button', { name: '连接' })).toBeEnabled());
    expect(screen.getByText('未连接')).toBeInTheDocument();
    expect(screen.queryByLabelText('edge-terminal-01 SSH 终端')).not.toBeInTheDocument();
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/^\/api\/v1\/assets\?/);
    expect(fetchMock.mock.calls[0][1]?.method ?? 'GET').toBe('GET');
  }, 15_000);

  it('waits for server closure after disconnect is accepted and then refreshes history', async () => {
    disconnectResponse = response({ status: 'terminating' }, 202);
    render(<TerminalPage initialAssetId="asset-1" />);
    await userEvent.click(await screen.findByRole('button', { name: '连接' }));
    await userEvent.click(await screen.findByRole('button', { name: '模拟连接成功' }));
    await userEvent.click(screen.getByRole('button', { name: '断开' }));
    expect(screen.getByLabelText('edge-terminal-01 SSH 终端')).toBeInTheDocument();
    expect(screen.getByText('正在断开')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '连接' })).toBeDisabled();
    expect(screen.queryByText('当前会话已断开')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '模拟断开完成' }));
    expect(screen.getByText('已关闭')).toBeInTheDocument();
    expect(screen.getByText('会话已由用户断开')).toBeInTheDocument();
    expect(screen.getByLabelText('会话结束时间')).toHaveAttribute('dateTime', '2026-11-03T09:00:00Z');
    expect(screen.getByRole('button', { name: '连接' })).toBeEnabled();
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls[3][0]).toBe('/api/v1/terminal/sessions/session-2/disconnect');
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/api/v1/terminal/sessions?'))).toHaveLength(2));
  }, 15_000);

  it('unlocks reconnecting after an abnormal closure while disconnect is pending', async () => {
    render(<TerminalPage initialAssetId="asset-1" />);
    await userEvent.click(await screen.findByRole('button', { name: '连接' }));
    await userEvent.click(await screen.findByRole('button', { name: '模拟连接成功' }));
    await userEvent.click(screen.getByRole('button', { name: '断开' }));
    await userEvent.click(screen.getByRole('button', { name: '模拟连接异常' }));
    expect(screen.getByText('连接失败')).toBeInTheDocument();
    expect(screen.getByText('终端连接已中断，请重新连接')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '连接' })).toBeEnabled();
    expect(screen.queryByText('正在断开')).not.toBeInTheDocument();
  }, 15_000);

  it('shows existing session end time and failure reason in recent sessions', async () => {
    vi.mocked(fetch).mockImplementation((input) => Promise.resolve(response(String(input).startsWith('/api/v1/assets?') ? [asset] : [{
      id: 'previous-session', asset_id: asset.id, asset_name: '最近的服务器',
      host: asset.host, operator: 'admin', status: 'failed',
      requested_at: '2026-11-03T08:00:00Z', connected_at: null,
      ended_at: '2026-11-03T09:00:00Z', failure_reason: '服务器连接超时',
    }])));
    render(<TerminalPage initialAssetId="asset-1" />);
    expect(await screen.findByText('服务器连接超时')).toBeInTheDocument();
    expect(screen.getByText(/^结束于 /)).toBeInTheDocument();
  });

  it('shows a revoked historical session with its Chinese status, end time and reason', async () => {
    vi.mocked(fetch).mockImplementation((input) => Promise.resolve(response(String(input).startsWith('/api/v1/assets?') ? [asset] : [{
      id: 'revoked-session', asset_id: asset.id, asset_name: '已撤销会话服务器',
      host: asset.host, operator: 'admin', status: 'revoked',
      requested_at: '2026-11-03T08:00:00Z', connected_at: '2026-11-03T08:01:00Z',
      ended_at: '2026-11-03T09:00:00Z', failure_reason: '当前账号已失去服务器访问权限',
    }])));
    render(<TerminalPage initialAssetId="asset-1" />);
    expect(await screen.findByText(/^权限已撤销 · /)).toBeInTheDocument();
    expect(screen.getByText('当前账号已失去服务器访问权限')).toBeInTheDocument();
    expect(screen.getByText(`结束于 ${new Date('2026-11-03T09:00:00Z').toLocaleString()}`)).toBeInTheDocument();
  });

  it('ignores a late disconnect rejection after the server confirms closure', async () => {
    let complete!: (value: Response) => void;
    disconnectResponse = new Promise<Response>((resolve) => { complete = resolve; });
    render(<TerminalPage initialAssetId="asset-1" />);
    await userEvent.click(await screen.findByRole('button', { name: '连接' }));
    await userEvent.click(await screen.findByRole('button', { name: '模拟连接成功' }));
    await userEvent.click(screen.getByRole('button', { name: '断开' }));
    await userEvent.click(screen.getByRole('button', { name: '模拟断开完成' }));
    await act(async () => complete(response({ detail: '迟到的断开错误' }, 503)));
    expect(screen.getByText('已关闭')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '连接' })).toBeEnabled();
    expect(screen.queryByText('迟到的断开错误')).not.toBeInTheDocument();
  }, 15_000);

  it('preserves the connection when disconnect is rejected', async () => {
    disconnectResponse = response({ detail: '断开被拒绝' }, 403);
    render(<TerminalPage initialAssetId="asset-1" />);
    await userEvent.click(await screen.findByRole('button', { name: '连接' }));
    await userEvent.click(await screen.findByRole('button', { name: '模拟连接成功' }));
    await userEvent.click(screen.getByRole('button', { name: '断开' }));
    expect(await screen.findByText('断开被拒绝')).toBeInTheDocument();
    expect(screen.getByLabelText('edge-terminal-01 SSH 终端')).toBeInTheDocument();
    expect(screen.getByText('已连接')).toBeInTheDocument();
  }, 15_000);

  it('ignores a disconnect response after switching assets', async () => {
    let complete!: (value: Response) => void;
    disconnectResponse = new Promise<Response>((resolve) => { complete = resolve; });
    const { rerender } = render(<TerminalPage initialAssetId="asset-1" />);
    await userEvent.click(await screen.findByRole('button', { name: '连接' }));
    await userEvent.click(await screen.findByRole('button', { name: '模拟连接成功' }));
    await userEvent.click(screen.getByRole('button', { name: '断开' }));
    rerender(<TerminalPage initialAssetId="asset-2" />);
    await act(async () => complete(response({ status: 'terminating' }, 202) as Response));
    expect(screen.getByText('未连接')).toBeInTheDocument();
    expect(screen.queryByText('已关闭')).not.toBeInTheDocument();
  }, 15_000);

  it('discards the old ticket when navigating to another asset', async () => {
    const { rerender } = render(<TerminalPage initialAssetId="asset-1" />);
    await screen.findByRole('button', { name: '连接' });
    await userEvent.click(screen.getByRole('button', { name: '连接' }));
    await screen.findByLabelText('edge-terminal-01 SSH 终端');
    rerender(<TerminalPage initialAssetId="asset-2" />);
    expect(screen.queryByLabelText('edge-terminal-01 SSH 终端')).not.toBeInTheDocument();
    expect(screen.getByText('未连接')).toBeInTheDocument();
  });

  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    disconnectResponse = response({ status: 'terminating' }, 202);
    vi.stubGlobal('fetch', vi.fn((input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/assets?')) return Promise.resolve(response([asset]) as Response);
      if (url.startsWith('/api/v1/terminal/sessions?')) return Promise.resolve(response([]) as Response);
      if (url === '/api/v1/terminal/tickets') return Promise.resolve(response({
        ticket: 'one-time-ticket', session_id: 'session-2',
        websocket_path: '/api/v1/terminal/ws', expires_at: '2026-09-08T08:02:00Z',
      }, 201) as Response);
      if (url.includes('/disconnect')) return Promise.resolve(disconnectResponse);
      return Promise.resolve(response({}) as Response);
    }));
  });
  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('connects through a one-time ticket without rendering session audit', async () => {
    render(<ConfigProvider><TerminalPage initialAssetId="asset-1" /></ConfigProvider>);
    await screen.findByRole('button', { name: '连接' });
    expect(screen.queryByText('会话审计')).not.toBeInTheDocument();
    expect(screen.getByText('AI 运维助手')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '连接' }));
    expect(await screen.findByLabelText('edge-terminal-01 SSH 终端')).toBeInTheDocument();
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/terminal/tickets');
    expect(String(fetchMock.mock.calls[2][1]?.body)).not.toContain('credential');
  });

  it('opens the selected asset in a new authenticated tab without exposing a ticket', async () => {
    render(<ConfigProvider><TerminalPage initialAssetId="asset-1" /></ConfigProvider>);
    const link = await screen.findByRole('link', { name: '新标签打开' });
    expect(link).toHaveAttribute('href', `${window.location.origin}/?terminal_asset=asset-1`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it('collapses and reopens the AI panel without removing the workspace', async () => {
    render(<ConfigProvider><TerminalPage initialAssetId="asset-1" /></ConfigProvider>);
    await screen.findByRole('button', { name: '收起 AI 助手' });
    const workspace = document.querySelector('.terminal-workspace');
    expect(workspace).not.toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '收起 AI 助手' }));
    expect(workspace).toHaveClass('terminal-workspace--assistant-collapsed');
    await userEvent.click(screen.getByRole('button', { name: '展开 AI 助手' }));
    expect(workspace).not.toHaveClass('terminal-workspace--assistant-collapsed');
  }, 15_000);
});
