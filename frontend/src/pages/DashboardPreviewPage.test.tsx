import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardPreviewPage } from './DashboardPreviewPage';

const snapshot = {
  assets: { total: 8, online: 7, offline: 0, unknown: 1, disabled: 0 },
  vulnerabilities: { total: 12, critical: 2, high: 3, medium: 4, low: 3, confirmed: 7, manual_confirmation: 2, pending: 12 },
  scans: { total: 3, queued: 1, running: 0, completed: 2, failed: 0, success_rate: 100 },
  knowledge: {
    active_version: '2026.09.03', active_advisory_count: 320, last_sync_status: 'completed',
    last_sync_at: '2026-09-03T08:00:00Z', freshness: '正常', stale: false,
    source_count: 9, healthy_source_count: 9, stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0,
  },
  recent_scans: [{ id: 'scan-1', name: '生产环境巡检', scan_type: 'comprehensive', status: 'completed', progress: 100, finding_count: 4, created_at: '2026-09-03T08:00:00Z' }],
  affected_assets: [], resource_alerts: { cpu: 1, memory: 0, disk: 1 },
  monitoring_assets: [{
    asset_id: 'asset-1', asset_name: 'edge-01', host: '10.0.0.8', lifecycle_status: 'enabled', connection_status: 'online',
    metrics: { cpu_percent: 91, memory_percent: 72, disk_percent: 88 }, collected_at: '2026-09-03T08:00:00Z',
    stale: false, active_alerts: 2, critical: 2, high: 2, pending_vulnerabilities: 4, recent_scan_at: '2026-09-03T08:00:00Z', cmdb_services: [],
  }],
  resource_trends: [
    { collected_at: '2026-09-03T07:00:00Z', cpu_percent: 48, memory_percent: 60, disk_percent: 80 },
    { collected_at: '2026-09-03T08:00:00Z', cpu_percent: 91, memory_percent: 72, disk_percent: 88 },
  ],
  active_alerts: [{ asset_id: 'asset-1', asset_name: 'edge-01', metric: 'cpu_percent', value: 91, threshold: 85, triggered_at: '2026-09-03T08:00:00Z' }],
  catalog: { businesses: 2, applications: 4, services: 6, apis: 12, linked_assets: 5, unlinked_assets: 3 },
  service_health: {
    summary: { total: 6, healthy: 4, degraded: 1, unavailable: 0, unobserved: 1 },
    services: [{
      business_id: 'business-1', business_name: '支付平台', application_id: 'app-1', application_name: '支付应用',
      service_id: 'service-1', service_name: '支付 API', service_code: 'PAY-API', service_type: 'api', status: 'degraded',
      asset_count: 1, freshness: 'fresh', stale_assets: 0, online_assets: 1, offline_assets: 0, uncollected_assets: 0, disabled_assets: 0,
      active_alerts: 2, pending_vulnerabilities: 4, critical: 2, high: 2,
      resource_peaks: { cpu_percent: 91, memory_percent: 72, disk_percent: 88 }, last_collected_at: '2026-09-03T08:00:00Z', assets: [],
    }],
  },
  operations: { active_alerts: 4, severe_alerts: 1, active_incidents: 2, severe_incidents: 1, running_tasks: 0, failed_tasks: 0, enabled_schedules: 2, active_probes: 4, failing_probes: 1, unobserved_probes: 0, pending_verification: 2 },
  attention_items: [{ id: 'alert-1', kind: 'alert', title: '数据库连接耗尽', detail: 'edge-01 · open', severity: 'critical', status: 'open', occurred_at: '2026-09-03T08:00:00Z', asset_id: 'asset-1', asset_name: 'edge-01', target: 'alerts' }],
  monitoring_mode: 'ssh_agentless_near_realtime' as const,
};

beforeEach(() => {
  sessionStorage.setItem('sentinel_admin_token', 'test-token');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

describe('DashboardPreviewPage', () => {
  it('presents real operational data in the redesigned hierarchy', async () => {
    render(<ConfigProvider><DashboardPreviewPage onNavigate={vi.fn()} /></ConfigProvider>);

    expect(await screen.findByRole('heading', { name: '综合总览' })).toBeInTheDocument();
    expect(screen.getByText('需要关注')).toBeInTheDocument();
    expect(screen.getByText('服务运行态势')).toBeInTheDocument();
    expect(screen.getByText('统一待办')).toBeInTheDocument();
    expect(screen.getByText('CMDB 编目')).toBeInTheDocument();
    expect(screen.getByText('资源使用趋势')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'CPU 内存 磁盘资源趋势图' })).toBeInTheDocument();
    expect(screen.getByText('数据库连接耗尽')).toBeInTheDocument();
    expect(screen.queryByText('服务器监控')).not.toBeInTheDocument();
  });

  it('keeps preview actions connected to existing pages', async () => {
    const onNavigate = vi.fn();
    render(<ConfigProvider><DashboardPreviewPage onNavigate={onNavigate} /></ConfigProvider>);

    await screen.findByText('生产环境巡检');
    fireEvent.click(screen.getByRole('button', { name: '进入服务监控' }));
    fireEvent.click(screen.getByRole('button', { name: '进入漏洞治理' }));

    expect(onNavigate).toHaveBeenNthCalledWith(1, 'observability');
    expect(onNavigate).toHaveBeenNthCalledWith(2, 'findings');
  });

  it('shows a recoverable error without leaving stale preview data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('接口不可用')));
    render(<ConfigProvider><DashboardPreviewPage onNavigate={vi.fn()} /></ConfigProvider>);

    expect(await screen.findByText('总览数据读取失败')).toBeInTheDocument();
    expect(screen.queryByText('数据库连接耗尽')).not.toBeInTheDocument();
  });
});
