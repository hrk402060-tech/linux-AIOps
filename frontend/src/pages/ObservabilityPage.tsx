import { useEffect, useState } from 'react';
import { Alert, Empty, Input, Select, Spin, Table, Tag, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Activity, RefreshCw, Server } from 'lucide-react';

import { getServiceObservability, type ObservableService, type ServiceObservabilityResponse } from '../api/observability';

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
  fresh: { label: '新鲜', color: 'green' },
  stale: { label: '陈旧', color: 'orange' },
  partial: { label: '部分新鲜', color: 'gold' },
  unobserved: { label: '未观测', color: 'default' },
};

export function ObservabilityPage() {
  const [data, setData] = useState(empty);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [api, holder] = message.useMessage();
  const load = async () => {
    setLoading(true); setError('');
    try { setData(await getServiceObservability({ query, status })); }
    catch (value) { const text = value instanceof Error ? value.message : '服务可观测性加载失败'; setError(text); api.error(text); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [status]);
  const columns: ColumnsType<ObservableService> = [
    { title: '服务', key: 'service', fixed: 'left', width: 240, render: (_, item) => <div className="stacked-cell"><strong>{item.service_name}</strong><small>{item.service_code} · {item.business_name} · {item.application_name}</small></div> },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: ObservableService['status']) => <Tag color={labels[value].color}>{labels[value].label}</Tag> },
    { title: '数据新鲜度', width: 130, render: (_, item) => <span><Tag color={freshnessLabels[item.freshness].color}>{freshnessLabels[item.freshness].label}</Tag>{item.stale_assets ? <small>{item.stale_assets} 台陈旧</small> : null}</span> },
    { title: '资产', width: 140, render: (_, item) => `${item.online_assets} 在线 / ${item.offline_assets} 离线 / ${item.uncollected_assets} 未观测` },
    { title: '资源峰值', width: 150, render: (_, item) => `CPU ${format(item.resource_peaks.cpu_percent)} · 内存 ${format(item.resource_peaks.memory_percent)}` },
    { title: '活动告警', dataIndex: 'active_alerts', width: 100, render: (value: number) => value ? <Tag color="red">{value}</Tag> : <span>0</span> },
    { title: '严重/高危', width: 110, render: (_, item) => `${item.critical} / ${item.high}` },
    { title: '最近采集', dataIndex: 'last_collected_at', width: 130, render: (value: string | null) => formatTime(value) },
    { title: '承载资产', width: 240, render: (_, item) => item.assets.length ? item.assets.map((asset) => `${asset.asset_name} · ${asset.host}`).join('、') : '暂无有效资产' },
  ];
  return <section className="observability-page">{holder}
    <div className="page-heading"><div><p className="eyebrow">运维中心 / 运行态势</p><h1>服务可观测性</h1></div><button className="icon-action" type="button" aria-label="刷新服务可观测性" onClick={() => void load()}><RefreshCw size={17} /></button></div>
    <Alert type="info" showIcon icon={<Activity size={17} />} message="SSH 无 Agent 准实时数据" description={`当前视图聚合已有监控快照、告警与漏洞结果；超过 ${formatDuration(data.freshness_threshold_seconds)} 未更新会标记为陈旧，未观测服务不会被标记为健康。`} />
    {error ? <Alert role="alert" type="error" message={error} action={<button type="button" className="link-button" onClick={() => void load()}>重试</button>} /> : null}
    {!loading && !error && !data.summary.total ? <Alert type="info" showIcon message="还没有可观测服务" description="请先在业务服务创建业务、应用和服务，并将服务绑定服务器；随后等待 SSH 无 Agent 采集产生快照。" /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}><div hidden={Boolean(error)} className={`observability-data-shell${loading ? ' is-loading' : ''}`}>
      <div className="metric-band observability-metrics"><div><span>服务总数</span><strong>{data.summary.total}</strong></div><div><span>健康</span><strong>{data.summary.healthy}</strong></div><div><span>降级</span><strong>{data.summary.degraded}</strong></div><div><span>不可用/未观测</span><strong>{data.summary.unavailable + data.summary.unobserved}</strong></div></div>
      <div className="observability-toolbar"><Input allowClear placeholder="搜索服务、编码、业务或应用" value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={() => void load()} /><Select allowClear placeholder="状态" value={status} onChange={setStatus} options={Object.entries(labels).map(([value, item]) => ({ value, label: item.label }))} /><button className="icon-action" type="button" aria-label="查询服务可观测性" onClick={() => void load()}><Server size={17} /></button></div>
      <div className="table-frame"><Table rowKey="service_id" columns={columns} dataSource={data.services} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1200 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务可观测数据，请先完成配置项服务建模和服务器绑定" /> }} /></div>
    </div></Spin>
  </section>;
}

function format(value: number | null) { return value === null ? '-' : `${value.toFixed(1)}%`; }
function formatTime(value: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '未采集'; }
function formatDuration(seconds: number) { return seconds >= 3600 ? `${Math.round(seconds / 3600)} 小时` : `${Math.round(seconds / 60)} 分钟`; }
