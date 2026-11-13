import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ObservabilityPage } from './ObservabilityPage';
import { getServiceObservability, type ServiceObservabilityResponse } from '../api/observability';

vi.mock('../api/observability', () => ({ getServiceObservability: vi.fn() }));

const snapshot: ServiceObservabilityResponse = {
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
    runtime_status: 'unobserved', health_scope: 'all', scope_complete: true,
    health_reasons: [{ code: 'asset_stale', message: '服务器快照已过期' }, { code: 'probe_failed', message: '订单健康检查失败' }],
    probe_summary: { configured: 1, active: 1, disabled: 0, healthy: 0, failed: 1, unobserved: 0, stale: 0, api_count: 1, covered_api_count: 1 },
    probes: [{ probe_id: 'probe-1', probe_name: '订单健康检查', asset_id: 'asset-1', api_id: 'api-1', status: 'failed', reason_code: 'http_status_mismatch', checked_at: '2026-09-15T06:00:00Z', latency_ms: 20, http_status: 503 }],
    alerts: [{ alert_id: 'alert-1', asset_id: 'asset-1', source: 'external', severity: 'high', status: 'open', message: '订单服务异常', updated_at: '2026-09-15T06:00:00Z' }],
    assets: [{ asset_id: 'asset-1', asset_name: 'api-01', host: '192.0.2.40', status: 'online', freshness: 'stale', collected_at: '2026-09-15T06:00:00Z' }],
  }],
  monitoring_mode: 'ssh_agentless_near_realtime' as const,
  freshness_threshold_seconds: 900,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getServiceObservability).mockResolvedValue(snapshot);
});

describe('ObservabilityPage', () => {
  it('shows service health and current catalog runtime context', async () => {
    render(<ConfigProvider><ObservabilityPage /></ConfigProvider>);

    expect(await screen.findByText('订单 API')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '服务监控' })).toBeInTheDocument();
    expect(screen.getByText(/订单平台.*订单应用/)).toBeInTheDocument();
    expect(screen.getByText('服务器快照已过期')).toBeInTheDocument();
    expect(screen.queryByText('SSH 无 Agent 准实时数据')).not.toBeInTheDocument();
    expect(screen.getByText('陈旧')).toBeInTheDocument();
  });

  it('opens evidence and passes the selected asset, probe and alert context without executing', async () => {
    const onOpenProbeHistory = vi.fn(), onOpenAlerts = vi.fn(), onOpenFindings = vi.fn(), onOpenTerminal = vi.fn(), onOpenAssistant = vi.fn();
    render(<ConfigProvider theme={{ token: { motion: false } }}><ObservabilityPage
      onOpenProbeHistory={onOpenProbeHistory} onOpenAlerts={onOpenAlerts}
      onOpenFindings={onOpenFindings} onOpenTerminal={onOpenTerminal} onOpenAssistant={onOpenAssistant} /></ConfigProvider>);
    await screen.findByText('订单 API');
    expect(onOpenAssistant).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '查看 订单 API 证据' }));
    expect(screen.getByText('订单健康检查')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 订单健康检查 历史' }));
    expect(onOpenProbeHistory).toHaveBeenCalledWith('service-1', { id: 'probe-1', name: '订单健康检查' });
    fireEvent.click(screen.getByRole('button', { name: '定位 订单服务异常 告警' }));
    expect(onOpenAlerts).toHaveBeenCalledWith('asset-1', 'external', 'alert-1');
    fireEvent.click(screen.getByRole('button', { name: '查看 api-01 告警' }));
    expect(onOpenAlerts).toHaveBeenLastCalledWith('asset-1');
    fireEvent.click(screen.getByRole('button', { name: '查看 api-01 漏洞' }));
    expect(onOpenFindings).toHaveBeenCalledWith('asset-1');
    fireEvent.click(screen.getByRole('button', { name: '打开 api-01 SSH' }));
    expect(onOpenTerminal).toHaveBeenCalledWith('asset-1');
    fireEvent.click(screen.getByRole('button', { name: '使用 AI 排查 api-01' }));
    expect(onOpenAssistant).toHaveBeenCalledWith('asset-1', expect.stringContaining('订单 API'));
    expect(onOpenAssistant.mock.calls[0][1]).toContain('服务器快照已过期');
  }, 15000);

  it('falls back to the asset activity list when monitoring evidence has no alert ID', async () => {
    vi.mocked(getServiceObservability).mockResolvedValue({ ...snapshot, services: [{
      ...snapshot.services[0], alerts: [{ ...snapshot.services[0].alerts[0], alert_id: null, source: 'monitoring' }],
    }] });
    const onOpenAlerts = vi.fn();
    render(<ObservabilityPage onOpenAlerts={onOpenAlerts} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看 订单 API 证据' }));
    fireEvent.click(screen.getByRole('button', { name: '定位 订单服务异常 告警' }));
    expect(onOpenAlerts).toHaveBeenCalledExactlyOnceWith('asset-1', 'activity', undefined);
  });

  it('does not present missing configuration or restricted visibility as complete service health', async () => {
    const service = { ...snapshot.services[0], status: 'unobserved' as const, runtime_status: 'healthy' as const,
      health_scope: 'authorized' as const, scope_complete: false,
      health_reasons: [{ code: 'probe_unconfigured', message: '尚未配置 API 拨测' }],
      probe_summary: { configured: 0, active: 0, disabled: 0, healthy: 0, failed: 0, unobserved: 0, stale: 0, api_count: 2, covered_api_count: 0 }, probes: [], alerts: [] };
    vi.mocked(getServiceObservability).mockResolvedValue({ ...snapshot, services: [service] });
    render(<ObservabilityPage />);
    expect(await screen.findByText('仅授权范围')).toBeInTheDocument();
    expect(screen.getByText('尚未配置 API 拨测')).toBeInTheDocument();
    expect(screen.getByText('0 / 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 订单 API 证据' }));
    expect(screen.getByText('尚未配置拨测目标')).toBeInTheDocument();
  }, 15000);

  it('keeps loading distinct from zero and supports query refresh', async () => {
    let finish: (value: ServiceObservabilityResponse) => void = () => undefined;
    vi.mocked(getServiceObservability).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    render(<ObservabilityPage />);
    expect(screen.getByText('正在加载真实数据')).toBeInTheDocument();
    expect(screen.queryByText('还没有可观测服务')).not.toBeInTheDocument();
    finish(snapshot);
    await screen.findByText('订单 API');
    fireEvent.change(screen.getByPlaceholderText('搜索服务、编码、业务或应用'), { target: { value: '订单' } });
    fireEvent.click(screen.getByRole('button', { name: '查询服务监控' }));
    await waitFor(() => expect(getServiceObservability).toHaveBeenLastCalledWith({ query: '订单', status: undefined }));
  }, 15000);
});
