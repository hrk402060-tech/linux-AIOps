import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Progress, Spin, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Activity, Database, FileWarning, Gauge, Network, RefreshCw, ScanSearch, Server, SquareTerminal } from 'lucide-react';
import { getDashboard } from '../api/dashboard';
import type { PageKey } from '../App';
import type { DashboardAttentionItem, DashboardMonitoringAsset, DashboardServiceHealthItem, DashboardSnapshot } from '../types/dashboard';

const emptySnapshot: DashboardSnapshot = {
  assets: { total: 0, online: 0, offline: 0, unknown: 0, disabled: 0 },
  vulnerabilities: { total: 0, critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0, pending: 0 },
  scans: { total: 0, queued: 0, running: 0, completed: 0, failed: 0, success_rate: 0 },
  knowledge: { active_version: null, active_advisory_count: 0, last_sync_status: null, last_sync_at: null, freshness: '未同步', stale: true, source_count: 0, healthy_source_count: 0, stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0 },
  recent_scans: [], affected_assets: [], resource_alerts: { cpu: 0, memory: 0, disk: 0 }, monitoring_assets: [], resource_trends: [], active_alerts: [],
  catalog: { businesses: 0, applications: 0, services: 0, apis: 0, linked_assets: 0, unlinked_assets: 0 },
  service_health: { summary: { total: 0, healthy: 0, degraded: 0, unavailable: 0, unobserved: 0 }, services: [] },
  operations: { active_alerts: 0, severe_alerts: 0, active_incidents: 0, severe_incidents: 0, running_tasks: 0, failed_tasks: 0, enabled_schedules: 0, active_probes: 0, failing_probes: 0, unobserved_probes: 0, pending_verification: 0 }, attention_items: [], monitoring_mode: 'ssh_agentless_near_realtime',
};
const states: Record<string, { label: string; color: string }> = { online: { label: '在线', color: 'green' }, offline: { label: '离线', color: 'red' }, uncollected: { label: '未采集', color: 'default' }, disabled: { label: '停用', color: 'gold' } };
const scanStates: Record<string, string> = { queued: '排队', scanning: '扫描中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' };
const metricLabels: Record<string, string> = { cpu_percent: 'CPU', memory_percent: '内存', disk_percent: '磁盘', availability: '可用性', load_1m: '负载', swap_percent: 'Swap', inode_percent: 'inode' };

export function DashboardPage({ onNavigate, onViewFindings, onOpenTerminal }: {
  onNavigate: (page: PageKey) => void;
  onViewFindings?: (assetId: string) => void;
  onOpenTerminal?: (assetId: string) => void;
}) {
  const [snapshot, setSnapshot] = useState(emptySnapshot); const [loading, setLoading] = useState(true); const [messageApi, holder] = message.useMessage();
  const [loadError, setLoadError] = useState('');
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true); setLoadError(''); setSnapshot(emptySnapshot);
    try {
      const next = await getDashboard();
      if (current === requestId.current) setSnapshot(next);
    } catch (error) {
      if (current !== requestId.current) return;
      const text = error instanceof Error ? error.message : '总览数据加载失败';
      setLoadError(text); messageApi.error(text);
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [messageApi]);
  useEffect(() => {
    void load();
    const expire = () => {
      requestId.current += 1;
      setSnapshot(emptySnapshot); setLoadError('会话已失效，请重新登录'); setLoading(false);
    };
    window.addEventListener('sentinel:session-expired', expire);
    return () => { requestId.current += 1; window.removeEventListener('sentinel:session-expired', expire); };
  }, [load]);
  const columns: ColumnsType<DashboardMonitoringAsset> = [
    { title: '服务器', key: 'asset', fixed: 'left', width: 170, render: (_, item) => <div className="stacked-cell"><strong>{item.asset_name}</strong><small>{item.host}</small></div> },
    { title: '状态', key: 'state', width: 118, render: (_, item) => <span><Tag color={states[item.connection_status]?.color}>{states[item.connection_status]?.label ?? item.connection_status}</Tag>{item.stale ? <Tag color="orange">陈旧</Tag> : null}</span> },
    { title: 'CPU', width: 78, render: (_, item) => percent(item.metrics.cpu_percent) }, { title: '负载', width: 70, render: (_, item) => number(item.metrics.load_1m) }, { title: '内存', width: 78, render: (_, item) => percent(item.metrics.memory_percent) }, { title: 'Swap', width: 78, render: (_, item) => percent(item.metrics.swap_percent) }, { title: '磁盘', width: 78, render: (_, item) => percent(item.metrics.disk_percent) }, { title: 'inode', width: 78, render: (_, item) => percent(item.metrics.inode_percent) },
    { title: '网络 收/发', width: 120, render: (_, item) => `${bytes(item.metrics.network_rx_bytes)} / ${bytes(item.metrics.network_tx_bytes)}` }, { title: '运行时间', width: 100, render: (_, item) => duration(item.metrics.uptime_seconds) },
    { title: '承载服务', key: 'services', width: 220, render: (_, item) => item.cmdb_services.length ? <div className="stacked-cell"><strong>{item.cmdb_services.slice(0, 2).map((service) => service.service_name).join('、')}</strong><small>{item.cmdb_services[0].business_name} · {item.cmdb_services[0].application_name}</small></div> : <span className="muted">未编目</span> },
    { title: '漏洞', width: 115, render: (_, item) => <span className="risk-inline"><b>{item.critical}</b> 严重 · {item.high} 高危</span> }, { title: '待处理', dataIndex: 'pending_vulnerabilities', width: 80 }, { title: '最近采集', width: 130, render: (_, item) => formatTime(item.collected_at) },
    { title: '操作', key: 'actions', fixed: 'right', width: 92, render: (_, item) => <span className="row-actions">
      <Tooltip title="查看漏洞"><Button type="text" aria-label={`查看 ${item.asset_name} 漏洞`} disabled={!onViewFindings} icon={<FileWarning size={16} />} onClick={() => onViewFindings?.(item.asset_id)} /></Tooltip>
      <Tooltip title={item.lifecycle_status !== 'enabled' ? '停用或归档资产不可连接' : '打开 SSH'}><Button type="text" aria-label={`打开 ${item.asset_name} SSH`} disabled={!onOpenTerminal || item.lifecycle_status !== 'enabled'} icon={<SquareTerminal size={16} />} onClick={() => onOpenTerminal?.(item.asset_id)} /></Tooltip>
    </span> },
  ];
  return <section className="dashboard-page">{holder}<div className="page-heading dashboard-heading"><div><p className="eyebrow">Linux 智能综合运维平台</p><h1>统一运维工作台</h1></div><div className="dashboard-actions"><Tooltip title="刷新"><Button aria-label="刷新" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip><Button type="primary" icon={<ScanSearch size={17} />} onClick={() => onNavigate('scans')}>发起扫描</Button></div></div><Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}>
    {loadError ? <Alert type="error" showIcon message="总览数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    <div hidden={Boolean(loadError)} className={`dashboard-data-shell${loading ? ' is-loading' : ''}`}>
    <div className="operations-metrics"><Metric icon={<Server size={18} />} label="服务器" value={snapshot.assets.total} detail={`${snapshot.assets.online} 在线 · ${snapshot.assets.offline} 离线 · ${snapshot.assets.unknown} 未采集 · ${snapshot.assets.disabled} 停用`} /><Metric icon={<Network size={18} />} label="服务" value={snapshot.catalog.services} detail={`${snapshot.catalog.linked_assets} 台已关联 · ${snapshot.catalog.unlinked_assets} 台未关联`} /><Metric icon={<Activity size={18} />} label="活动告警" value={snapshot.operations.active_alerts} detail={`${snapshot.operations.severe_alerts} 个严重 · ${snapshot.operations.active_incidents} 个活动故障`} alert={snapshot.operations.severe_alerts > 0} /><Metric icon={<Gauge size={18} />} label="扫描任务" value={snapshot.scans.total} detail={`${snapshot.scans.queued} 排队 · ${snapshot.scans.running} 执行中 · ${snapshot.scans.completed} 完成 · ${snapshot.scans.failed} 失败`} alert={snapshot.scans.running > 0 || snapshot.scans.failed > 0} /><Metric icon={<Database size={18} />} label="知识库" value={snapshot.knowledge.freshness} detail={`${snapshot.knowledge.healthy_source_count}/${snapshot.knowledge.source_count} 数据源正常 · ${formatTime(snapshot.knowledge.last_sync_at)}`} alert={snapshot.knowledge.stale} /></div>
    <div className="ops-grid dashboard-platform-grid"><section className="ops-panel"><PanelHeading title="服务运行态势" subtitle={`${snapshot.service_health.summary.total} 个服务 · SSH 无 Agent 准实时采集`} /><div className="health-summary"><span>健康 <b>{snapshot.service_health.summary.healthy}</b></span><span className="warning">降级 <b>{snapshot.service_health.summary.degraded}</b></span><span className="danger">不可用 <b>{snapshot.service_health.summary.unavailable}</b></span><span className="muted">未观测 <b>{snapshot.service_health.summary.unobserved}</b></span></div>{snapshot.service_health.services.length ? <div className="service-health-list">{snapshot.service_health.services.slice(0, 5).map((item) => <ServiceHealthRow key={item.service_id} item={item} />)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务目录数据" />}</section><section className="ops-panel"><PanelHeading title="统一待办" subtitle="来自告警、故障和巡检的当前事项" />{snapshot.attention_items.length ? <div className="attention-list">{snapshot.attention_items.slice(0, 6).map((item) => <AttentionRow key={`${item.kind}-${item.id}`} item={item} onNavigate={onNavigate} />)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办事项" />}</section><section className="ops-panel catalog-panel"><PanelHeading title="配置项编目" subtitle="业务、应用、服务与 API 的当前目录" /><div className="catalog-stats"><CatalogStat label="业务" value={snapshot.catalog.businesses} /><CatalogStat label="应用" value={snapshot.catalog.applications} /><CatalogStat label="服务" value={snapshot.catalog.services} /><CatalogStat label="API" value={snapshot.catalog.apis} /></div><div className="catalog-assets"><span>资产关系</span><strong>{snapshot.catalog.linked_assets}</strong><small>已关联 · {snapshot.catalog.unlinked_assets} 台未关联</small></div><Button type="link" icon={<Network size={15} />} onClick={() => onNavigate('cmdb')}>进入配置项管理</Button></section></div>
    <div className="ops-section-heading"><div><strong>服务器准实时监控</strong><span>SSH 无 Agent 采集，不代表秒级实时；归档资产不计入，停用资产不自动采集</span></div></div><div className="table-frame dashboard-server-table"><Table rowKey="asset_id" columns={columns} dataSource={snapshot.monitoring_assets} pagination={{ pageSize: 8, showSizeChanger: false }} scroll={{ x: 1360 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务器监控数据" /> }} /></div>
    <div className="ops-grid"><section className="ops-panel impact-panel"><PanelHeading title="业务影响面" subtitle="按当前资产与配置项服务关系聚合" />{snapshot.monitoring_assets.length ? <div className="impact-list">{snapshot.monitoring_assets.slice(0, 8).map((item) => <div className="impact-row" key={item.asset_id}><div className="impact-asset"><strong>{item.asset_name}</strong><small>{item.host} · {item.cmdb_services.length ? item.cmdb_services.map((service) => service.service_name).join('、') : '未编目'}</small></div><div className="impact-risk"><b>{item.critical + item.high}</b><span>严重/高危</span></div><div className="impact-risk"><b>{item.active_alerts}</b><span>活动告警</span></div><Button type="link" icon={<Network size={15} />} onClick={() => onNavigate('cmdb')} aria-label={`查看 ${item.asset_name} 的配置项`}>配置项</Button></div>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无业务影响数据" />}</section><section className="ops-panel trend-panel"><PanelHeading title="资源趋势" subtitle="最近 24 个准实时采样" /><div className="trend-legend"><span className="cpu">CPU</span><span className="memory">内存</span><span className="disk">磁盘</span></div>{snapshot.resource_trends.length ? <div className="trend-bars">{snapshot.resource_trends.map((item) => <Tooltip key={item.collected_at} title={`${formatTime(item.collected_at)} CPU ${item.cpu_percent}% 内存 ${item.memory_percent}% 磁盘 ${item.disk_percent}%`}><span><i className="cpu" style={{ height: `${item.cpu_percent}%` }} /><i className="memory" style={{ height: `${item.memory_percent}%` }} /><i className="disk" style={{ height: `${item.disk_percent}%` }} /></span></Tooltip>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势样本" />}</section><section className="ops-panel"><PanelHeading title="漏洞态势" subtitle="按资产 + CVE 最新有效状态去重" /><div className="vuln-posture"><div><strong>{snapshot.vulnerabilities.critical + snapshot.vulnerabilities.high}</strong><span>严重 / 高危</span></div><div><strong>{snapshot.vulnerabilities.manual_confirmation}</strong><span>需人工确认</span></div></div><Progress percent={ratio(snapshot.vulnerabilities.critical + snapshot.vulnerabilities.high, snapshot.vulnerabilities.pending)} strokeColor="#aa2e2e" showInfo={false} /><Button type="link" onClick={() => onNavigate('findings')}>查看待处理漏洞</Button></section><section className="ops-panel"><PanelHeading title="活动告警" subtitle="持续超限后触发" /><Button type="link" onClick={() => onNavigate('alerts')} aria-label="进入告警中心">进入告警中心</Button>{snapshot.active_alerts.length ? <div className="alert-list">{snapshot.active_alerts.slice(0, 6).map((item) => <div key={`${item.asset_id}-${item.metric}`}><span><strong>{item.asset_name}</strong><small>{metricLabels[item.metric] ?? item.metric} · {item.value.toFixed(1)} / {item.threshold.toFixed(1)}</small></span><Tag color="red">告警</Tag></div>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无活动告警" />}</section><section className="ops-panel"><PanelHeading title="任务状态" subtitle="最近扫描任务" />{snapshot.recent_scans.length ? <div className="task-list">{snapshot.recent_scans.map((item) => <button key={item.id} type="button" onClick={() => onNavigate('scans')}><span><strong>{item.name}</strong><small>{formatTime(item.created_at)} · {item.finding_count} 个漏洞</small></span><Tag>{scanStates[item.status] ?? item.status}</Tag></button>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无扫描任务" />}</section></div>
    </div>
  </Spin></section>;
}
function ServiceHealthRow({ item }: { item: DashboardServiceHealthItem }) {
  const labels = { healthy: '健康', degraded: '降级', unavailable: '不可用', unobserved: '未观测' };
  const freshness = item.freshness === 'stale'
    ? ` · ${item.stale_assets} 台数据陈旧`
    : '';
  return <div className="service-health-row"><span><strong>{item.service_name}</strong><small>{item.business_name} · {item.asset_count} 台资产 · {item.active_alerts} 个告警{freshness}</small></span><Tag color={item.status === 'healthy' ? 'green' : item.status === 'degraded' ? 'orange' : 'red'}>{labels[item.status]}</Tag></div>;
}
const attentionStatusLabels: Record<string, string> = {
  open: '待处理', triggered: '已触发', acknowledged: '已确认', in_progress: '处理中',
  pending_verification: '待验证', resolved: '人工登记完成', closed: '人工关闭', ignored: '已忽略',
  risk_accepted: '风险接受', failed: '失败', unhealthy: '异常', error: '异常',
  healthy: '正常', success: '成功',
};
function AttentionRow({ item, onNavigate }: { item: DashboardAttentionItem; onNavigate: (page: PageKey) => void }) {
  const targetPages: PageKey[] = ['alerts', 'incidents', 'findings', 'scans', 'cmdb', 'assets', 'observability', 'probes', 'automation'];
  const target = targetPages.includes(item.target as PageKey) ? item.target as PageKey : 'dashboard';
  const statusLabel = attentionStatusLabels[item.status] ?? item.status;
  const detail = item.detail.endsWith(` · ${item.status}`)
    ? `${item.detail.slice(0, -item.status.length)}${statusLabel}` : item.detail;
  return <button type="button" className="attention-row" onClick={() => onNavigate(target)}><span><strong>{item.title}</strong><small>{detail} · {formatTime(item.occurred_at)}</small></span><Tag color={item.severity === 'critical' ? 'red' : 'orange'}>{statusLabel}</Tag></button>;
}
function CatalogStat({ label, value }: { label: string; value: number }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function Metric({ icon, label, value, detail, alert = false }: { icon: React.ReactNode; label: string; value: number | string; detail: string; alert?: boolean }) { return <div className={alert ? 'metric-alert' : ''}><span className="ops-metric-icon">{icon}</span><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>; }
function PanelHeading({ title, subtitle }: { title: string; subtitle: string }) { return <div className="panel-heading"><div><strong>{title}</strong><span>{subtitle}</span></div></div>; }
function ratio(value: number, total: number) { return total ? Math.round(value * 100 / total) : 0; }
function percent(value: unknown) { return typeof value === 'number' ? `${value.toFixed(1)}%` : '-'; }
function number(value: unknown) { return typeof value === 'number' ? value.toFixed(2) : '-'; }
function bytes(value: unknown) { if (typeof value !== 'number') return '-'; if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)}G`; if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)}M`; return `${Math.round(value / 1024)}K`; }
function duration(value: unknown) { if (typeof value !== 'number') return '-'; return `${Math.floor(value / 86400)}天 ${Math.floor(value % 86400 / 3600)}时`; }
function formatTime(value: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '未采集'; }
