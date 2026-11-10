import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerMonitoringPreviewPage } from './ServerMonitoringPreviewPage';
import * as api from '../api/monitoring';
import { assetFixture, fleetFixture, snapshotFixture, trendsFixture } from './server-monitoring/testFixtures';
import type { MonitorSnapshot } from '../types/monitoring';

vi.setConfig({ testTimeout: 30_000 });
vi.mock('../api/monitoring', () => ({
  getFleet: vi.fn(), getFleetTrends: vi.fn(), getMonitorHistory: vi.fn(),
  collectMonitor: vi.fn(), saveMonitorRules: vi.fn(),
}));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getFleet).mockResolvedValue(fleetFixture());
  vi.mocked(api.getFleetTrends).mockResolvedValue(trendsFixture);
  vi.mocked(api.getMonitorHistory).mockResolvedValue([]);
});
function page(canManage = false) { return render(<ConfigProvider><ServerMonitoringPreviewPage canManage={canManage} /></ConfigProvider>); }
async function select(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }));
  const node = await screen.findByTitle(option);
  fireEvent.click(node);
}

describe('server monitoring preview', () => {
  it('has a genuine loading state and renders real fleet data with honest missing values', async () => {
    page();
    expect(screen.getByRole('status', { name: '正在加载服务器监控' })).toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    await screen.findByText('GitLab');
    const offline = within(screen.getByTestId('server-b'));
    expect(offline.getByText('离线')).toBeInTheDocument();
    expect(offline.getAllByText('—')).toHaveLength(4);
    expect(offline.queryByText('5.0%')).not.toBeInTheDocument();
    expect(api.collectMonitor).not.toHaveBeenCalled();
    expect(api.saveMonitorRules).not.toHaveBeenCalled();
  });

  it('combines search, group and status and uses the filtered asset IDs for global aggregation', async () => {
    page(); await screen.findByText('GitLab');
    await select('服务器分组', '生产');
    expect(screen.queryByText('Jenkins')).not.toBeInTheDocument();
    await select('服务器状态', '在线');
    fireEvent.change(screen.getByPlaceholderText('搜索服务器名称或 IP'), { target: { value: '192.168.10.11' } });
    expect(screen.getByText('GitLab')).toBeInTheDocument();
    await waitFor(() => expect(api.getFleetTrends).toHaveBeenLastCalledWith(['a'], 24, expect.any(AbortSignal)));
    fireEvent.change(screen.getByPlaceholderText('搜索服务器名称或 IP'), { target: { value: 'no-match' } });
    expect(await screen.findByText('没有匹配的服务器，可调整搜索或筛选条件')).toBeInTheDocument();
    await waitFor(() => expect(api.getFleetTrends).toHaveBeenLastCalledWith([], 24, expect.any(AbortSignal)));
  });

  it('binds detail, real interfaces, partitions, CPU cores and load lines to the selected server', async () => {
    const fleet = fleetFixture();
    fleet.assets[0] = assetFixture('a', {
      cpu_count: 16, cpu_percent: 18, memory_percent: 30, load_1m: 2, load_5m: 1, load_15m: 0.8,
      network_interfaces: [{ name: 'ens9', rx_bytes_per_second: 2048, tx_bytes_per_second: 1024 }],
      disks: [{ device: '/dev/sdb', mount: '/srv/data', total_bytes: 10240, used_bytes: 1024,
        available_bytes: 9216, used_percent: 10, inode_observed: false }],
    });
    vi.mocked(api.getFleet).mockResolvedValue(fleet);
    vi.mocked(api.getMonitorHistory).mockResolvedValue([fleet.assets[0].latest_snapshot!]);
    page(); await screen.findByText('GitLab');
    fireEvent.click(screen.getByRole('button', { name: '查看 GitLab 监控详情' }));
    const detail = await screen.findByRole('region', { name: 'GitLab 监控详情' });
    expect(await within(detail).findByRole('img', { name: 'GitLab 1、5、15 分钟负载趋势' })).toBeInTheDocument();
    expect(within(detail).getByText('16 逻辑 CPU 核')).toBeInTheDocument();
    expect(within(detail).getByText('ens9')).toBeInTheDocument();
    expect(within(detail).getByText('/srv/data')).toBeInTheDocument();
    expect(within(detail).getByText('10.0 KiB')).toBeInTheDocument();
    expect(within(detail).queryByText('4 逻辑 CPU 核')).not.toBeInTheDocument();
    expect(api.getMonitorHistory).toHaveBeenCalledWith('a', 24, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: '收起 GitLab 详情' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'GitLab 监控详情' })).not.toBeInTheDocument());
  });

  it('discards late detail responses after switching servers and aborts old requests', async () => {
    let resolveA: ((value: MonitorSnapshot[]) => void) | undefined;
    let signalA: AbortSignal | undefined;
    vi.mocked(api.getMonitorHistory).mockImplementation((id, _hours, signal) => {
      if (id === 'a') { signalA = signal; return new Promise((resolve) => { resolveA = resolve; }); }
      return Promise.resolve([snapshotFixture('b', { load_1m: 4 })]);
    });
    page(); await screen.findByText('GitLab');
    fireEvent.click(screen.getByRole('button', { name: '查看 GitLab 监控详情' }));
    await waitFor(() => expect(resolveA).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: '查看 Jenkins 监控详情' }));
    await screen.findByRole('region', { name: 'Jenkins 监控详情' });
    resolveA!([snapshotFixture('a', { network_interfaces: [{ name: 'must-not-leak' }] })]);
    expect(screen.queryByText('must-not-leak')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'GitLab 监控详情' })).not.toBeInTheDocument();
    expect(signalA?.aborted).toBe(true);
  });

  it('shows errors instead of stale healthy data and refreshes using a new request', async () => {
    vi.mocked(api.getFleet).mockRejectedValueOnce(new Error('服务连接失败'));
    page(); expect(await screen.findByText('服务连接失败')).toBeInTheDocument();
    expect(screen.queryByText('在线服务器')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(await screen.findByText('GitLab')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新服务器监控' }));
    await waitFor(() => expect(api.getFleet).toHaveBeenCalledTimes(3));
  });

  it('saves validated per-asset rules only through an explicit manager action', async () => {
    vi.mocked(api.saveMonitorRules).mockResolvedValue({});
    page(true); await screen.findByText('GitLab');
    fireEvent.click(screen.getByRole('button', { name: '查看 GitLab 监控详情' }));
    fireEvent.click(await screen.findByRole('button', { name: '资源规则' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'CPU 使用率关注阈值' }), { target: { value: '95' } });
    expect(screen.getByRole('button', { name: '保存规则' })).toBeDisabled();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'CPU 使用率关注阈值' }), { target: { value: '60' } });
    fireEvent.click(screen.getByRole('button', { name: '保存规则' }));
    await waitFor(() => expect(api.saveMonitorRules).toHaveBeenCalledWith('a', expect.objectContaining({ cpu: { warning: 60, critical: 90 } })));
    expect(api.collectMonitor).not.toHaveBeenCalled();
  });
});
