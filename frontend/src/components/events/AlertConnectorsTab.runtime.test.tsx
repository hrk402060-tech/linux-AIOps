import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getAlertConnectors, getAlertConnectorRuntime, getAlertConnectorReceipts,
  type AlertConnector, type ConnectorRuntime,
} from '../../api/externalAlerts';
import { AlertConnectorsTab } from './AlertConnectorsTab';

vi.mock('../../api/externalAlerts', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../api/externalAlerts')>(),
  getAlertConnectors: vi.fn(), getAlertConnectorRuntime: vi.fn(), getAlertConnectorReceipts: vi.fn(),
}));

const connector: AlertConnector = {
  id: 'connector-1', name: '生产告警', source_type: 'prometheus', status: 'active',
  secret_masked: '********', operator: 'admin', created_at: '2026-11-06T00:00:00Z',
  updated_at: '2026-11-06T00:00:00Z', rotated_at: null,
};
const runtime: ConnectorRuntime = {
  connector_id: connector.id, health: 'healthy', success_count: 12, failure_count: 3,
  last_received_at: '2026-11-06T01:00:00Z', last_success_at: '2026-11-06T01:00:00Z',
  last_failure_at: '2026-11-06T00:30:00Z', last_error_code: null, last_error: null,
  observed_since: '2026-11-06T00:20:00Z',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function view(role: 'admin' | 'operator' | 'auditor' = 'admin') {
  return <ConfigProvider theme={{ token: { motion: false } }}>
    <AlertConnectorsTab currentUser={{ role }} />
  </ConfigProvider>;
}

beforeEach(() => {
  vi.mocked(getAlertConnectors).mockResolvedValue([connector]);
  vi.mocked(getAlertConnectorRuntime).mockResolvedValue([runtime]);
  vi.mocked(getAlertConnectorReceipts).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 });
});
afterEach(() => vi.resetAllMocks());

describe('AlertConnectorsTab runtime summary', () => {
  it('shows loading until runtime arrives and never substitutes zero counts', async () => {
    const pending = deferred<ConnectorRuntime[]>();
    vi.mocked(getAlertConnectorRuntime).mockReturnValue(pending.promise);
    render(view());
    await screen.findByText(connector.name);
    expect(screen.getAllByText('运行数据加载中').length).toBeGreaterThan(0);
    expect(screen.queryByText('成功 0 / 失败 0')).toBeNull();
    await act(async () => { pending.resolve([runtime]); });
    expect(await screen.findByText('最近接收成功')).toBeInTheDocument();
    expect(screen.getByText('成功 12 / 失败 3')).toBeInTheDocument();
    expect(screen.getByText(/历史不回填/)).toBeInTheDocument();
    expect(screen.getByText(/不代表诊断或通知已验证/)).toBeInTheDocument();
  });

  it.each(['disabled', 'unverified', 'error'] as const)('uses a precise %s receipt state', async (health) => {
    vi.mocked(getAlertConnectorRuntime).mockResolvedValue([{ ...runtime, health }]);
    const labels = { disabled: '已停用', unverified: '尚无接收记录', error: '最近接收失败' };
    render(view());
    expect(await screen.findByText(labels[health])).toBeInTheDocument();
    expect(screen.queryByText('运行正常')).toBeNull();
  });

  it('clears stale counts when runtime refresh fails and hides raw errors', async () => {
    vi.mocked(getAlertConnectorRuntime).mockResolvedValueOnce([runtime])
      .mockRejectedValueOnce(new Error('Traceback secret=private-signature'));
    render(view());
    await screen.findByText('成功 12 / 失败 3');
    fireEvent.click(screen.getByRole('button', { name: '刷新连接器' }));
    expect(await screen.findByText('运行数据加载失败，请刷新后重试')).toBeInTheDocument();
    expect(screen.queryByText('成功 12 / 失败 3')).toBeNull();
    expect(screen.queryByText('成功 0 / 失败 0')).toBeNull();
    expect(screen.queryByText(/Traceback|private-signature/)).toBeNull();
  });

  it.each([
    ['signature_invalid', '签名不匹配'],
    ['Traceback secret=private-code', '接收失败'],
  ])('maps the latest failure code %s without exposing the raw error', async (code, label) => {
    vi.mocked(getAlertConnectorRuntime).mockResolvedValue([{
      ...runtime, health: 'error', last_error_code: code, last_error: 'Traceback secret=private-message',
    }]);
    render(view());
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByText(/Traceback|private-code|private-message/)).toBeNull();
  });

  it.each(['operator', 'auditor'] as const)('does not request or expose runtime data to %s', async (role) => {
    render(view(role));
    await screen.findByText(connector.name);
    expect(getAlertConnectorRuntime).not.toHaveBeenCalled();
    expect(getAlertConnectorReceipts).not.toHaveBeenCalled();
    expect(screen.queryByText('运行状态')).toBeNull();
    expect(screen.queryByRole('button', { name: '接收记录 生产告警' })).toBeNull();
  });

  it('opens the selected connector receipts only on explicit administrator action', async () => {
    render(view());
    await screen.findByText('成功 12 / 失败 3');
    expect(getAlertConnectorReceipts).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '接收记录 生产告警' }));
    expect(await screen.findByRole('dialog', { name: '接收记录：生产告警' })).toBeInTheDocument();
    await waitFor(() => expect(getAlertConnectorReceipts).toHaveBeenCalledWith(connector.id, 0, 20, undefined));
  });

  it('ignores an older runtime response after a newer manual refresh', async () => {
    const late = deferred<ConnectorRuntime[]>();
    vi.mocked(getAlertConnectorRuntime).mockReturnValueOnce(late.promise).mockResolvedValueOnce([
      { ...runtime, health: 'error', success_count: 1, failure_count: 8 },
    ]);
    render(view());
    await screen.findByText(connector.name);
    fireEvent.click(screen.getByRole('button', { name: '刷新连接器' }));
    await screen.findByText('成功 1 / 失败 8');
    await act(async () => { late.resolve([runtime]); });
    expect(screen.getByText('成功 1 / 失败 8')).toBeInTheDocument();
    expect(screen.queryByText('成功 12 / 失败 3')).toBeNull();
  });

  it('clears runtime and the open receipts context immediately on administrator demotion', async () => {
    const late = deferred<ConnectorRuntime[]>();
    vi.mocked(getAlertConnectorRuntime).mockReturnValueOnce(late.promise);
    const { rerender } = render(view());
    fireEvent.click(await screen.findByRole('button', { name: '接收记录 生产告警' }));
    await screen.findByRole('dialog', { name: '接收记录：生产告警' });
    rerender(view('auditor'));
    await act(async () => { late.resolve([runtime]); });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText('成功 12 / 失败 3')).toBeNull();
    expect(screen.queryByRole('button', { name: '接收记录 生产告警' })).toBeNull();
    expect(getAlertConnectorRuntime).toHaveBeenCalledTimes(1);
  });
});
