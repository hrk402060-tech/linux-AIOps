import { render, screen } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ObservabilityPage } from './ObservabilityPage';

const snapshot = {
  summary: { total: 2, healthy: 1, degraded: 1, unavailable: 0, unobserved: 0 },
  services: [{
    business_id: 'business-1', business_name: '订单平台', application_id: 'app-1',
    application_name: '订单应用', service_id: 'service-1', service_name: '订单 API',
    service_code: 'ORDER-API', service_type: 'api', status: 'degraded',
    freshness: 'stale', stale_assets: 1, asset_count: 1,
    online_assets: 1, offline_assets: 0, uncollected_assets: 0, disabled_assets: 0,
    active_alerts: 1, pending_vulnerabilities: 3, critical: 1, high: 2,
    resource_peaks: { cpu_percent: 92, memory_percent: 73, disk_percent: 61 },
    last_collected_at: '2026-09-15T06:00:00Z',
    assets: [{ asset_id: 'asset-1', asset_name: 'api-01', host: '192.0.2.40', status: 'online', freshness: 'stale', collected_at: '2026-09-15T06:00:00Z' }],
  }],
  monitoring_mode: 'ssh_agentless_near_realtime' as const,
  freshness_threshold_seconds: 900,
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
});

describe('ObservabilityPage', () => {
  it('shows service health and current catalog runtime context', async () => {
    render(<ConfigProvider><ObservabilityPage /></ConfigProvider>);

    expect(await screen.findByText('订单 API')).toBeInTheDocument();
    expect(screen.getByText('服务可观测性')).toBeInTheDocument();
    expect(screen.getByText(/订单平台.*订单应用/)).toBeInTheDocument();
    expect(screen.getByText(/api-01.*192.0.2.40/)).toBeInTheDocument();
    expect(screen.getByText('SSH 无 Agent 准实时数据')).toBeInTheDocument();
    expect(screen.getByText('陈旧')).toBeInTheDocument();
    expect(screen.getByText(/超过 15 分钟/)).toBeInTheDocument();
  });
});
