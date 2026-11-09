import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from './DashboardPage';

const snapshot = {
  assets: { total: 8, online: 5, offline: 1, unknown: 1, disabled: 1 },
  vulnerabilities: {
    total: 12, critical: 2, high: 3, medium: 4, low: 3,
    confirmed: 7, manual_confirmation: 5, pending: 12,
  },
  scans: { total: 10, queued: 2, running: 1, completed: 8, failed: 1, success_rate: 89 },
  knowledge: {
    active_version: '2026.09.03', active_advisory_count: 320,
    last_sync_status: 'completed', last_sync_at: '2026-09-03T08:00:00Z',
    freshness: '正常', stale: false, source_count: 9, healthy_source_count: 9,
    stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0,
  },
  recent_scans: [{
    id: 'scan-1', name: '生产环境巡检', scan_type: 'comprehensive', status: 'completed',
    progress: 100, finding_count: 4, created_at: '2026-09-03T08:00:00Z',
  }],
  affected_assets: [{
    asset_id: 'asset-1', asset_name: 'edge-01', total: 4, critical: 2, high: 2,
  }],
  resource_alerts: { cpu: 1, memory: 0, disk: 1 },
  monitoring_assets: [{
    asset_id: 'asset-1', asset_name: 'edge-01', host: '10.0.0.8',
    lifecycle_status: 'enabled', connection_status: 'online',
    metrics: { cpu_percent: 91, load_1m: 3.2, memory_percent: 72, swap_percent: 0,
      disk_percent: 88, inode_percent: 35, network_rx_bytes: 1024,
      network_tx_bytes: 2048, uptime_seconds: 172800 },
    collected_at: '2026-09-03T08:00:00Z', stale: false, active_alerts: 2,
    critical: 2, high: 2, pending_vulnerabilities: 4, recent_scan_at: '2026-09-03T08:00:00Z',
    cmdb_services: [{ business_id: 'business-1', business_name: '支付平台', application_id: 'app-1', application_name: '支付应用', service_id: 'service-1', service_name: '支付 API', service_type: 'api', relation_role: 'primary' }],
  }],
  resource_trends: [{ collected_at: '2026-09-03T08:00:00Z', cpu_percent: 91, memory_percent: 72, disk_percent: 88 }],
  active_alerts: [{ asset_id: 'asset-1', asset_name: 'edge-01', metric: 'cpu_percent', value: 91, threshold: 85, triggered_at: '2026-09-03T08:00:00Z' }],
  catalog: { businesses: 2, applications: 4, services: 6, apis: 12, linked_assets: 5, unlinked_assets: 3 },
  service_health: {
    summary: { total: 6, healthy: 3, degraded: 1, unavailable: 1, unobserved: 1 },
    services: [{
      business_id: 'business-1', business_name: '支付平台', application_id: 'app-1', application_name: '支付应用',
      service_id: 'service-1', service_name: '支付 API', service_code: 'PAY-API', service_type: 'api', status: 'degraded',
      asset_count: 1, online_assets: 1, offline_assets: 0, uncollected_assets: 0, disabled_assets: 0,
      active_alerts: 2, pending_vulnerabilities: 4, critical: 2, high: 2,
      resource_peaks: { cpu_percent: 91, memory_percent: 72, disk_percent: 88 },
      last_collected_at: '2026-09-03T08:00:00Z', assets: [{ asset_id: 'asset-1', asset_name: 'edge-01', host: '10.0.0.8', status: 'online' }],
    }],
  },
  operations: {
    active_alerts: 4, severe_alerts: 1, active_incidents: 2, severe_incidents: 1,
    running_tasks: 3, failed_tasks: 1, enabled_schedules: 5, active_probes: 4,
    failing_probes: 1, unobserved_probes: 1, pending_verification: 2,
  },
  attention_items: [
    { id: 'alert-1', kind: 'alert', title: '数据库连接耗尽', detail: 'edge-01 · open', severity: 'critical', status: 'open', occurred_at: '2026-09-03T08:00:00Z', asset_id: 'asset-1', asset_name: 'edge-01', target: 'alerts' },
    { id: 'incident-1', kind: 'incident', title: '支付接口不可用', detail: 'edge-01 · in_progress', severity: 'high', status: 'in_progress', occurred_at: '2026-09-03T08:00:00Z', asset_id: 'asset-1', asset_name: 'edge-01', target: 'incidents' },
  ],
  monitoring_mode: 'ssh_agentless_near_realtime' as const,
};

beforeEach(() => {
  sessionStorage.setItem('sentinel_admin_token', 'test-token');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

describe('DashboardPage', () => {
  it('shows the operational snapshot and recent risk activity', async () => {
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} /></ConfigProvider>);

    expect(await screen.findByText('生产环境巡检')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '统一运维工作台' })).toBeInTheDocument();
    expect(screen.getByText('Linux 智能综合运维平台')).toBeInTheDocument();
    expect(screen.getByText('服务运行态势')).toBeInTheDocument();
    expect(screen.getByText('统一待办')).toBeInTheDocument();
    expect(screen.getByText('数据库连接耗尽')).toBeInTheDocument();
    expect(screen.getByText(/6 个服务/)).toBeInTheDocument();
    expect(screen.getAllByText('edge-01').length).toBeGreaterThan(0);
    expect(screen.getByText(/9\/9 数据源正常/)).toBeInTheDocument();
    expect(screen.getByText(/2 排队 · 1 执行中/)).toBeInTheDocument();
    expect(screen.getByText('服务器准实时监控')).toBeInTheDocument();
    expect(screen.getAllByText('edge-01').length).toBeGreaterThan(0);
    expect(screen.getByText('按资产 + CVE 最新有效状态去重')).toBeInTheDocument();
    expect(screen.getAllByText('支付 API').length).toBeGreaterThan(0);
    expect(screen.getByText('业务影响面')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '监控中心' })).not.toBeInTheDocument();
  });

  it('routes operators to the focused work pages', async () => {
    const onNavigate = vi.fn();
    render(<ConfigProvider><DashboardPage onNavigate={onNavigate} /></ConfigProvider>);
    await screen.findByText('生产环境巡检');

    fireEvent.click(screen.getByRole('button', { name: '进入告警中心' }));
    fireEvent.click(screen.getByRole('button', { name: '查看待处理漏洞' }));

    expect(onNavigate).toHaveBeenNthCalledWith(1, 'alerts');
    expect(onNavigate).toHaveBeenNthCalledWith(2, 'findings');
  });

  it('provides asset-scoped vulnerability and SSH actions', async () => {
    const onViewFindings = vi.fn();
    const onOpenTerminal = vi.fn();
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} onViewFindings={onViewFindings} onOpenTerminal={onOpenTerminal} /></ConfigProvider>);

    await screen.findByText('生产环境巡检');
    fireEvent.click(screen.getByRole('button', { name: '查看 edge-01 漏洞' }));
    fireEvent.click(screen.getByRole('button', { name: '打开 edge-01 SSH' }));

    expect(onViewFindings).toHaveBeenCalledWith('asset-1');
    expect(onOpenTerminal).toHaveBeenCalledWith('asset-1');
  });

  it('translates dashboard attention status into Chinese', async () => {
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} /></ConfigProvider>);

    await screen.findByText('数据库连接耗尽');
    const item = within(screen.getByRole('button', { name: /数据库连接耗尽/ }));
    expect(item.getByText('待处理')).toBeInTheDocument();
    expect(item.getByText(/edge-01 · 待处理/)).toBeInTheDocument();
    expect(screen.queryByText('open')).not.toBeInTheDocument();
  });

  it('translates failed and probe statuses without changing ordinary detail text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...snapshot,
      attention_items: [
        { ...snapshot.attention_items[0], id: 'failed-task', title: '扫描失败', detail: 'edge-01 · failed', status: 'failed', target: 'scans' },
        { ...snapshot.attention_items[0], id: 'probe-error', title: '接口异常', detail: 'edge-01 · error', status: 'error', target: 'probes' },
        { ...snapshot.attention_items[0], id: 'probe-unhealthy', title: '接口不可用', detail: 'edge-01 · unhealthy', status: 'unhealthy', target: 'probes' },
        { ...snapshot.attention_items[0], id: 'open-text', title: '端口 open 检查', detail: 'edge-01 · port open', status: 'triggered', target: 'alerts' },
        { ...snapshot.attention_items[0], id: 'resolved', title: '人工登记', detail: 'edge-01 · resolved', status: 'resolved', target: 'incidents' },
      ],
    }) }));
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} /></ConfigProvider>);

    await screen.findByText('扫描失败');
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getAllByText('异常')).toHaveLength(2);
    expect(screen.getByText(/port open/)).toBeInTheDocument();
    expect(screen.getByText('人工登记完成')).toBeInTheDocument();
    expect(screen.queryByText('已恢复')).not.toBeInTheDocument();
    expect(screen.queryByText('failed')).not.toBeInTheDocument();
    expect(screen.queryByText('error')).not.toBeInTheDocument();
  });

  it.each(['disabled', 'archived'])('does not offer SSH for a %s asset', async (lifecycle) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      ...snapshot, monitoring_assets: snapshot.monitoring_assets.map((item) => ({ ...item, lifecycle_status: lifecycle })),
    }) }));
    const onOpenTerminal = vi.fn();
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} onOpenTerminal={onOpenTerminal} /></ConfigProvider>);
    await screen.findByText('生产环境巡检');
    expect(screen.getByRole('button', { name: '打开 edge-01 SSH' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '打开 edge-01 SSH' }));
    expect(onOpenTerminal).not.toHaveBeenCalled();
  });

  it('clears the previous snapshot during refresh and never restores it on failure', async () => {
    let fail!: (reason: Error) => void;
    const pending = new Promise<Response>((_, reject) => { fail = reject; });
    vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => snapshot } as Response)
      .mockReturnValueOnce(pending);
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} /></ConfigProvider>);
    await screen.findByText('生产环境巡检');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(screen.queryByText('生产环境巡检')).not.toBeInTheDocument();
    await act(async () => fail(new Error('权限校验失败')));
    expect(await screen.findByText('总览数据读取失败')).toBeInTheDocument();
    expect(screen.queryByText('生产环境巡检')).not.toBeInTheDocument();
  });

  it('ignores a pending snapshot after the session expires', async () => {
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(new Promise<Response>((done) => { resolve = done; }));
    render(<ConfigProvider><DashboardPage onNavigate={vi.fn()} /></ConfigProvider>);
    act(() => window.dispatchEvent(new Event('sentinel:session-expired')));
    await act(async () => resolve({ ok: true, json: async () => snapshot } as Response));
    expect(screen.queryByText('生产环境巡检')).not.toBeInTheDocument();
    expect(screen.getByText('总览数据读取失败')).toBeInTheDocument();
  });
});
