import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Alert, Button, Empty, Input, Select, Spin, Tag, message } from 'antd';
import { Activity, Cpu, HardDrive, MemoryStick, RefreshCw, Server } from 'lucide-react';

import { getDashboard } from '../api/dashboard';
import type { DashboardMonitoringAsset, DashboardSnapshot } from '../types/dashboard';

type MonitoringData = Pick<DashboardSnapshot, 'assets' | 'monitoring_assets' | 'resource_trends' | 'monitoring_mode'>;
const emptyData: MonitoringData = {
  assets: { total: 0, online: 0, offline: 0, unknown: 0, disabled: 0 },
  monitoring_assets: [], resource_trends: [], monitoring_mode: 'ssh_agentless_near_realtime',
};
const statusLabels: Record<string, { label: string; color: string }> = {
  online: { label: '在线', color: 'green' }, offline: { label: '离线', color: 'red' },
  uncollected: { label: '未采集', color: 'default' }, disabled: { label: '停用', color: 'gold' },
};

export function ServerMonitoringPage() {
  const [data, setData] = useState<MonitoringData>(emptyData);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [api, holder] = message.useMessage();
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const snapshot = await getDashboard();
      setData({ assets: snapshot.assets, monitoring_assets: snapshot.monitoring_assets,
        resource_trends: snapshot.resource_trends, monitoring_mode: snapshot.monitoring_mode });
    } catch (value) {
      const text = value instanceof Error ? value.message : '服务器监控加载失败';
      setError(text); api.error(text);
    } finally { setLoading(false); }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  const assets = useMemo(() => data.monitoring_assets.filter((item) => {
    const text = `${item.asset_name} ${item.host}`.toLocaleLowerCase();
    return (!query.trim() || text.includes(query.trim().toLocaleLowerCase()))
      && (!status || item.connection_status === status);
  }), [data.monitoring_assets, query, status]);
  const attentionCount = data.monitoring_assets.filter((item) => item.connection_status !== 'online' || item.stale || item.active_alerts > 0).length;

  return <section className="server-monitoring-page">{holder}
    <div className="page-heading">
      <div><p className="eyebrow">工作台 / 综合总览</p><h1>服务器监控</h1><p className="page-subtitle">集中查看服务器连接状态、资源使用和采集新鲜度。</p></div>
      <div className="page-heading-actions"><Tag color="blue">SSH 无 Agent · 准实时</Tag><Button aria-label="刷新服务器监控" icon={<RefreshCw size={16} />} loading={loading} onClick={() => void load()}>刷新</Button></div>
    </div>
    {error ? <Alert role="alert" type="error" showIcon message="服务器监控读取失败" description={error} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载监控数据' : undefined}>
      <div hidden={Boolean(error)} className={`server-monitoring-content${loading ? ' is-loading' : ''}`}>
        <div className="server-monitoring-kpis">
          <MonitorKpi label="服务器总数" value={data.assets.total} tone="neutral" icon={<Server size={18} />} />
          <MonitorKpi label="在线" value={data.assets.online} tone="safe" icon={<Activity size={18} />} />
          <MonitorKpi label="离线" value={data.assets.offline} tone="danger" icon={<HardDrive size={18} />} />
          <MonitorKpi label="未采集 / 停用" value={data.assets.unknown + data.assets.disabled} tone="warning" icon={<Cpu size={18} />} />
          <MonitorKpi label="需要关注" value={attentionCount} tone={attentionCount ? 'danger' : 'safe'} icon={<MemoryStick size={18} />} />
        </div>
        <div className="server-monitoring-toolbar">
          <Input allowClear value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索服务器名称或 IP" />
          <Select allowClear value={status} onChange={setStatus} placeholder="全部状态" options={Object.entries(statusLabels).map(([value, item]) => ({ value, label: item.label }))} />
        </div>
        {!loading && !assets.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={data.monitoring_assets.length ? '没有符合筛选条件的服务器' : '暂无服务器监控数据，请先添加服务器并完成 SSH 连通性检查'} /> : null}
        <div className="server-monitoring-grid">{assets.map((asset) => <ServerCard key={asset.asset_id} asset={asset} />)}</div>
        <section className="server-monitoring-trend panel">
          <div className="panel-heading"><div><strong>资源趋势</strong><span>最近采集的 CPU、内存和磁盘使用率</span></div></div>
          <ResourceTrend data={data.resource_trends} />
        </section>
      </div>
    </Spin>
  </section>;
}

function MonitorKpi({ label, value, tone, icon }: { label: string; value: number; tone: string; icon: ReactNode }) {
  return <div className={`server-monitoring-kpi is-${tone}`}><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}
function ServerCard({ asset }: { asset: DashboardMonitoringAsset }) {
  const state = statusLabels[asset.connection_status] ?? statusLabels.uncollected;
  return <article className="server-monitoring-card">
    <div className="server-monitoring-card-head"><div><strong>{asset.asset_name}</strong><small>{asset.host}</small></div><Tag color={state.color}>{state.label}</Tag></div>
    <div className="server-monitoring-card-meta"><span className={`server-monitoring-dot is-${asset.connection_status}`} />{asset.stale ? '数据陈旧' : asset.collected_at ? `最近采集 ${formatTime(asset.collected_at)}` : '等待首次采集'}<span>{asset.active_alerts ? `${asset.active_alerts} 个告警` : '无活动告警'}</span></div>
    <div className="server-monitoring-metrics"><Metric label="CPU" value={metric(asset, 'cpu_percent')} /><Metric label="内存" value={metric(asset, 'memory_percent')} /><Metric label="磁盘" value={metric(asset, 'disk_percent')} /><Metric label="负载" value={metric(asset, 'load_1m')} /></div>
    <div className="server-monitoring-card-foot"><span>待处理漏洞 {asset.pending_vulnerabilities}</span><span>严重 {asset.critical} · 高危 {asset.high}</span></div>
  </article>;
}
function Metric({ label, value }: { label: string; value: number | null }) {
  const safe = value === null ? 0 : Math.min(Math.max(value, 0), 100);
  return <div><div className="server-monitoring-metric-label"><span>{label}</span><strong>{value === null ? '-' : `${value.toFixed(1)}${label === '负载' ? '' : '%'}`}</strong></div><i><b style={{ width: `${value === null ? 0 : safe}%` }} /></i></div>;
}
function ResourceTrend({ data }: { data: MonitoringData['resource_trends'] }) {
  if (!data.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势样本" />;
  const width = 760; const height = 180; const pad = 18;
  const point = (index: number, value: number) => `${pad + (index * (width - pad * 2)) / Math.max(data.length - 1, 1)},${height - pad - (Math.min(Math.max(value, 0), 100) * (height - pad * 2)) / 100}`;
  const series = [{ key: 'cpu_percent' as const, label: 'CPU', color: '#277655' }, { key: 'memory_percent' as const, label: '内存', color: '#347aa3' }, { key: 'disk_percent' as const, label: '磁盘', color: '#c27a2c' }];
  return <div className="server-monitoring-chart"><div className="server-monitoring-legend">{series.map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="服务器资源趋势图"><line x1={pad} y1={pad} x2={width - pad} y2={pad} /><line x1={pad} y1={height / 2} x2={width - pad} y2={height / 2} /><line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} />{series.map((item) => <polyline key={item.key} points={data.map((row, index) => point(index, row[item.key])).join(' ')} style={{ stroke: item.color }} />)}</svg></div>;
}
function metric(asset: DashboardMonitoringAsset, key: string) { const value = asset.metrics[key]; return typeof value === 'number' ? value : null; }
function formatTime(value: string) { return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)); }
