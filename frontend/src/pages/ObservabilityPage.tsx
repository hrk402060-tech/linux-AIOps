import { useEffect, useRef, useState, type Key } from 'react';
import { Alert, Button, Empty, Input, Select, Spin, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ChevronDown, ChevronRight, RefreshCw, Search } from 'lucide-react';

import { getServiceObservability, type ObservableService, type ServiceObservabilityResponse } from '../api/observability';
import { ServiceMonitoringDetail, formatEvidenceTime, type ServiceMonitoringActions } from '../components/ServiceMonitoringDetail';
import './service-monitoring.css';

const empty: ServiceObservabilityResponse = {
  summary: { total: 0, healthy: 0, degraded: 0, unavailable: 0, unobserved: 0 },
  services: [], monitoring_mode: 'ssh_agentless_near_realtime',
  freshness_threshold_seconds: 900,
};
const labels: Record<ObservableService['status'], { label: string; color: string }> = {
  healthy: { label: '健康', color: 'green' }, degraded: { label: '降级', color: 'orange' },
  unavailable: { label: '不可用', color: 'red' }, unobserved: { label: '未观测', color: 'default' },
};
const freshnessLabels: Record<ObservableService['freshness'], { label: string; color: string }> = {
  fresh: { label: '有效', color: 'green' },
  stale: { label: '陈旧', color: 'orange' },
  partial: { label: '部分有效', color: 'gold' },
  unobserved: { label: '未观测', color: 'default' },
};

export function ObservabilityPage(actions: ServiceMonitoringActions = {}) {
  const [data, setData] = useState(empty);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<Key[]>([]);
  const request = useRef(0);
  const [api, holder] = message.useMessage();
  const load = async () => {
    const token = ++request.current;
    setLoading(true); setError('');
    try {
      const result = await getServiceObservability({ query, status });
      if (token === request.current) { setData(normalizeResponse(result)); setHasLoaded(true); }
    } catch (value) {
      if (token === request.current) {
        const text = value instanceof Error ? value.message : '服务监控加载失败';
        setError(text); api.error(text);
      }
    } finally { if (token === request.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { ++request.current; }; }, [status]);
  const columns = serviceColumns();
  return <section className="observability-page">{holder}
    <div className="page-heading"><h1>服务监控</h1><Button aria-label="刷新服务监控" icon={<RefreshCw size={17} />} onClick={() => void load()} /></div>
    {error ? <Alert role="alert" type="error" message={error} action={<button type="button" className="link-button" onClick={() => void load()}>重试</button>} /> : null}
    {!loading && !error && !data.summary.total ? <Alert type="info" showIcon message="还没有可观测服务" description="请先在业务服务创建业务、应用和服务，关联服务器并配置 API 拨测。" /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}><div hidden={Boolean(error)} className={`observability-data-shell${loading && !hasLoaded ? ' is-loading' : ''}`}>
      <div className="metric-band observability-metrics">{([['服务总数', data.summary.total], ['健康', data.summary.healthy], ['降级', data.summary.degraded], ['不可用', data.summary.unavailable], ['未观测', data.summary.unobserved]] as const).map(([label, value]) => <div key={label}><span>{label}</span><strong>{hasLoaded ? value : '—'}</strong></div>)}</div>
      <div className="observability-toolbar"><Input allowClear placeholder="搜索服务、编码、业务或应用" value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={() => void load()} /><Select allowClear aria-label="服务监控状态" placeholder="状态" value={status} onChange={(value) => { setExpanded([]); setStatus(value); }} options={Object.entries(labels).map(([value, item]) => ({ value, label: item.label }))} /><Button aria-label="查询服务监控" icon={<Search size={17} />} onClick={() => void load()} /></div>
      <div className="table-frame"><Table rowKey="service_id" columns={columns} dataSource={data.services} pagination={{ pageSize: 10, showSizeChanger: false, onChange: () => setExpanded([]) }} scroll={{ x: 1600 }}
        expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: (keys) => setExpanded([...keys]), expandedRowRender: (service) => <ServiceMonitoringDetail service={service} {...actions} />, expandIcon: ({ expanded: isExpanded, onExpand, record }) => <button type="button" className="service-expand" aria-label={`${isExpanded ? '收起' : '查看'} ${record.service_name} 证据`} aria-expanded={isExpanded} onClick={(event) => onExpand(record, event)}>{isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button> }}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配服务；请调整筛选或在业务服务完成建模" /> }} /></div>
    </div></Spin>
  </section>;
}

function serviceColumns(): ColumnsType<ObservableService> {
  return [
    { title: '服务', key: 'service', fixed: 'left', width: 230, render: (_, item) => <div className="stacked-cell"><strong>{item.service_name}</strong><small>{item.service_code} · {item.business_name} · {item.application_name}</small>{!item.scope_complete ? <Tag>仅授权范围</Tag> : null}</div> },
    { title: '综合状态', width: 115, render: (_, item) => <div className="stacked-cell"><Tag color={labels[item.status].color}>{labels[item.status].label}</Tag><small>主机：{labels[item.runtime_status].label}</small></div> },
    { title: '状态依据', width: 230, render: (_, item) => <ul className="service-health-reasons">{item.health_reasons.map((reason) => <li key={reason.code}>{reason.message}</li>)}</ul> },
    { title: 'API 覆盖', width: 130, render: (_, item) => <div className="stacked-cell"><strong>{item.probe_summary.covered_api_count} / {item.probe_summary.api_count}</strong><small>{item.probe_summary.healthy} 通过 · {item.probe_summary.failed} 失败</small><small>{item.probe_summary.stale} 过期 · {item.probe_summary.unobserved} 未检查</small></div> },
    { title: '数据新鲜度', width: 125, render: (_, item) => <div className="stacked-cell"><Tag color={freshnessLabels[item.freshness].color}>{freshnessLabels[item.freshness].label}</Tag>{item.stale_assets ? <small>{item.stale_assets} 台陈旧</small> : null}</div> },
    { title: '服务器', width: 155, render: (_, item) => <div className="stacked-cell"><span>{item.online_assets} 在线 / {item.offline_assets} 离线</span><small>{item.uncollected_assets} 未观测 / {item.disabled_assets} 停用</small></div> },
    { title: '资源峰值', width: 160, render: (_, item) => <div className="stacked-cell"><span>CPU {format(item.resource_peaks.cpu_percent)}</span><small>内存 {format(item.resource_peaks.memory_percent)} · 磁盘 {format(item.resource_peaks.disk_percent)}</small></div> },
    { title: '活动告警', dataIndex: 'active_alerts', width: 100, render: (value: number) => value ? <Tag color="red">{value}</Tag> : 0 },
    { title: '严重/高危漏洞', width: 125, render: (_, item) => `${item.critical} / ${item.high}` },
    { title: '最近采集', dataIndex: 'last_collected_at', width: 170, render: formatEvidenceTime },
    { title: '承载资产', width: 220, render: (_, item) => item.assets.length ? item.assets.map((asset) => `${asset.asset_name} · ${asset.host}`).join('、') : '暂无有效资产' },
  ];
}

function format(value: number | null): string { return value === null ? '—' : `${value.toFixed(1)}%`; }

function normalizeResponse(result: ServiceObservabilityResponse): ServiceObservabilityResponse {
  return {
    ...result,
    services: result.services.map((service) => ({
      ...service,
      runtime_status: service.runtime_status ?? 'unobserved',
      health_scope: service.health_scope ?? 'authorized',
      scope_complete: service.scope_complete ?? false,
      health_reasons: service.health_reasons?.length ? service.health_reasons : [{ code: 'health_unknown', message: '健康依据暂不可用' }],
      probe_summary: service.probe_summary ?? { configured: 0, active: 0, disabled: 0, healthy: 0, failed: 0, unobserved: 0, stale: 0, api_count: 0, covered_api_count: 0 },
      probes: service.probes ?? [],
      alerts: service.alerts ?? [],
    })),
  };
}
