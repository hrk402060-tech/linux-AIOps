import { fireEvent, render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerMonitoringPage } from './ServerMonitoringPage';
import type { DashboardSnapshot } from '../types/dashboard';

const snapshot: DashboardSnapshot = {
  assets: { total: 3, online: 1, offline: 1, unknown: 1, disabled: 0 },
  vulnerabilities: { total: 0, critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0, pending: 0 },
  scans: { total: 0, queued: 0, running: 0, completed: 0, failed: 0, success_rate: 0 },
  knowledge: { active_version: null, active_advisory_count: 0, last_sync_status: null, last_sync_at: null, freshness: '未同步', stale: true, source_count: 0, healthy_source_count: 0, stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0 },
  recent_scans: [], affected_assets: [], resource_alerts: { cpu: 0, memory: 0, disk: 0 },
  monitoring_assets: [
    { asset_id: 'asset-1', asset_name: 'gitlab', host: '192.168.10.11', lifecycle_status: 'enabled', connection_status: 'online', metrics: { cpu_percent: 42.5, memory_percent: 61, disk_percent: 70, load_1m: 1.2 }, collected_at: '2026-11-10T08:00:00Z', stale: false, active_alerts: 0, critical: 0, high: 0, pending_vulnerabilities: 0, recent_scan_at: null, cmdb_services: [] },
    { asset_id: 'asset-2', asset_name: 'jenkins', host: '192.168.10.12', lifecycle_status: 'enabled', connection_status: 'offline', metrics: {}, collected_at: '2026-11-10T07:00:00Z', stale: true, active_alerts: 2, critical: 1, high: 1, pending_vulnerabilities: 2, recent_scan_at: null, cmdb_services: [] },
  ],
  resource_trends: [{ collected_at: '2026-11-10T08:00:00Z', cpu_percent: 42.5, memory_percent: 61, disk_percent: 70 }],
  active_alerts: [], catalog: { businesses: 0, applications: 0, services: 0, apis: 0, linked_assets: 0, unlinked_assets: 0 },
  service_health: { summary: { total: 0, healthy: 0, degraded: 0, unavailable: 0, unobserved: 0 }, services: [] },
  operations: { active_alerts: 0, severe_alerts: 0, active_incidents: 0, severe_incidents: 0, running_tasks: 0, failed_tasks: 0, enabled_schedules: 0, active_probes: 0, failing_probes: 0, unobserved_probes: 0, pending_verification: 0 },
  attention_items: [], monitoring_mode: 'ssh_agentless_near_realtime',
};

beforeEach(() => {
  sessionStorage.setItem('sentinel_admin_token', 'test-token');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

describe('ServerMonitoringPage', () => {
  it('shows real server status and resource metrics', async () => {
    render(<ConfigProvider><ServerMonitoringPage /></ConfigProvider>);

    expect(await screen.findByRole('heading', { name: '服务器监控' })).toBeInTheDocument();
    expect(screen.getByText('gitlab')).toBeInTheDocument();
    expect(screen.getByText('jenkins')).toBeInTheDocument();
    expect(screen.getAllByText('在线').length).toBeGreaterThan(0);
    expect(screen.getAllByText('离线').length).toBeGreaterThan(0);
    expect(screen.queryByText('监控数据来自最近一次只读采集')).not.toBeInTheDocument();
    expect(screen.queryByText('离线、未采集或陈旧数据不会被视为正常；平台只展示指标，不执行远程修改。')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: '服务器资源趋势图' })).toBeInTheDocument();
  });

  it('filters servers by status and host', async () => {
    render(<ConfigProvider><ServerMonitoringPage /></ConfigProvider>);
    await screen.findByText('gitlab');

    fireEvent.change(screen.getByPlaceholderText('搜索服务器名称或 IP'), { target: { value: 'jenkins' } });
    expect(screen.queryByText('gitlab')).not.toBeInTheDocument();
    expect(screen.getByText('jenkins')).toBeInTheDocument();
  });
});
