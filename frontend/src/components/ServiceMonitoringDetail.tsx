import { Button, Empty, Tag } from 'antd';
import { Bot, History, SquareTerminal, Bell } from 'lucide-react';
import type { ObservableService, ServiceProbeEvidence } from '../api/observability';

export interface ServiceMonitoringActions {
  onOpenProbeHistory?: (serviceId: string, probe: { id: string; name: string }) => void;
  onOpenAlerts?: (assetId: string, tab?: 'activity' | 'external') => void;
  onOpenTerminal?: (assetId: string) => void;
  onOpenAssistant?: (assetId: string, context: string) => void;
}

const probeStates = {
  healthy: { label: '通过', color: 'green' }, failed: { label: '失败', color: 'red' },
  stale: { label: '结果过期', color: 'orange' }, unobserved: { label: '未检查', color: 'default' },
  disabled: { label: '已停用', color: 'default' },
};
const probeReasons: Record<string, string> = {
  timeout: '请求超时', tls_error: 'TLS 校验失败', connection_error: '连接失败',
  http_status_mismatch: 'HTTP 状态不符合预期', config_changed: '目标配置已变更',
  context_changed: '关联关系已变更', api_changed: 'API 配置已变更',
};
const assetStates: Record<string, string> = { online: '在线', offline: '离线', disabled: '停用', unobserved: '未采集', uncollected: '未采集' };

export function serviceEvidenceContext(service: ObservableService): string {
  return [
    `服务：${service.service_name}（${service.service_code}）；业务：${service.business_name}；应用：${service.application_name}`,
    ...service.health_reasons.map((reason) => reason.message),
    ...service.probes.map((probe) => `${probe.probe_name}：${probeStates[probe.status].label}；检查时间：${probe.checked_at ?? '未检查'}`),
  ].join('\n').slice(0, 12000);
}

export function ServiceMonitoringDetail({ service, ...actions }: { service: ObservableService } & ServiceMonitoringActions) {
  return <div className="service-health-detail" aria-label={`${service.service_name} 监控证据`}>
    <section><h3>API 拨测</h3>
      {service.probes.length ? <div className="service-evidence-list">{service.probes.map((probe) =>
        <ProbeEvidence key={probe.probe_id} probe={probe} serviceId={service.service_id} onOpen={actions.onOpenProbeHistory} />)}</div>
        : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未配置拨测目标" />}
    </section>
    <section><h3>活动告警</h3>
      {service.alerts.length ? <div className="service-evidence-list">{service.alerts.map((alert, index) =>
        <div className="service-evidence-row" key={alert.alert_id ?? `${alert.asset_id}:${index}`}>
          <div><Tag color={['critical', 'high'].includes(alert.severity) ? 'red' : 'orange'}>{alert.source === 'monitoring' ? '服务器监控' : '外部告警'}</Tag><strong>{alert.message}</strong><small>{formatEvidenceTime(alert.updated_at)}</small></div>
          {actions.onOpenAlerts ? <Button size="small" icon={<Bell size={14} />} aria-label={`定位 ${alert.message} 告警`} onClick={() => actions.onOpenAlerts?.(alert.asset_id, alert.alert_id ? 'external' : 'activity')}>查看告警</Button> : null}
        </div>)}</div> : <span className="service-evidence-empty">当前范围无活动告警</span>}
    </section>
    <section className="service-health-assets"><h3>承载服务器</h3>
      {service.assets.length ? service.assets.map((asset) => <div className="service-evidence-row" key={asset.asset_id}>
        <div><strong>{asset.asset_name} · {asset.host}</strong><small>{assetStates[asset.status] ?? '状态未知'} · {asset.freshness === 'stale' ? '数据过期' : asset.freshness === 'fresh' ? '数据有效' : '缺少有效快照'} · {formatEvidenceTime(asset.collected_at)}</small></div>
        <div className="service-evidence-actions">
          {actions.onOpenAlerts ? <Button size="small" icon={<Bell size={14} />} aria-label={`查看 ${asset.asset_name} 告警`} onClick={() => actions.onOpenAlerts?.(asset.asset_id)}>告警</Button> : null}
          {actions.onOpenTerminal ? <Button size="small" icon={<SquareTerminal size={14} />} disabled={asset.status === 'disabled'} aria-label={`打开 ${asset.asset_name} SSH`} onClick={() => actions.onOpenTerminal?.(asset.asset_id)}>SSH</Button> : null}
          {actions.onOpenAssistant ? <Button size="small" icon={<Bot size={14} />} disabled={asset.status === 'disabled'} aria-label={`使用 AI 排查 ${asset.asset_name}`} onClick={() => actions.onOpenAssistant?.(asset.asset_id, serviceEvidenceContext(service))}>AI 排查</Button> : null}
        </div>
      </div>) : <span className="service-evidence-empty">尚未关联有效服务器</span>}
    </section>
  </div>;
}

function ProbeEvidence({ probe, serviceId, onOpen }: { probe: ServiceProbeEvidence; serviceId: string; onOpen?: ServiceMonitoringActions['onOpenProbeHistory'] }) {
  const state = probeStates[probe.status];
  return <div className="service-evidence-row"><div><Tag color={state.color}>{state.label}</Tag><strong>{probe.probe_name}</strong>
    <small>{probe.status === 'failed' ? `${probeReasons[probe.reason_code ?? ''] ?? '检查失败'} · ` : ''}{formatEvidenceTime(probe.checked_at)}{probe.latency_ms !== null ? ` · ${probe.latency_ms} ms` : ''}{probe.http_status !== null ? ` · HTTP ${probe.http_status}` : ''}</small>
  </div>{onOpen ? <Button size="small" icon={<History size={14} />} aria-label={`查看 ${probe.probe_name} 历史`} onClick={() => onOpen(serviceId, { id: probe.probe_id, name: probe.probe_name })}>检查历史</Button> : null}</div>;
}

export function formatEvidenceTime(value: string | null): string {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '无采集时间';
}
