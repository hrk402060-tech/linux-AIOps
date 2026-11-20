import { platformDateTimeOptions } from '../utils/platformPreferences';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Empty, Spin, Tag, Tooltip, message } from 'antd';
import {
  Activity, AlertTriangle, Bell, Database, ListChecks, Network, RefreshCw, ScanSearch, Server, ShieldAlert,
} from 'lucide-react';

import { getDashboard } from '../api/dashboard';
import type { PageKey } from '../App';
import type { DashboardAttentionItem, DashboardServiceHealthItem, DashboardSnapshot } from '../types/dashboard';

const emptySnapshot: DashboardSnapshot = {
  assets: { total: 0, online: 0, offline: 0, unknown: 0, disabled: 0 },
  vulnerabilities: { total: 0, critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0, pending: 0 },
  scans: { total: 0, queued: 0, running: 0, completed: 0, failed: 0, success_rate: 0 },
  knowledge: {
    active_version: null, active_advisory_count: 0, last_sync_status: null, last_sync_at: null,
    freshness: '未同步', stale: true, source_count: 0, healthy_source_count: 0,
    stale_source_count: 0, failed_source_count: 0, never_synced_source_count: 0,
  },
  recent_scans: [], affected_assets: [], resource_alerts: { cpu: 0, memory: 0, disk: 0 },
  monitoring_assets: [], resource_trends: [], active_alerts: [], catalog: {
    businesses: 0, applications: 0, services: 0, apis: 0, linked_assets: 0, unlinked_assets: 0,
  }, service_health: { summary: { total: 0, healthy: 0, degraded: 0, unavailable: 0, unobserved: 0 }, services: [] },
  operations: {
    active_alerts: 0, severe_alerts: 0, active_incidents: 0, severe_incidents: 0, running_tasks: 0,
    failed_tasks: 0, enabled_schedules: 0, active_probes: 0, failing_probes: 0, unobserved_probes: 0,
    pending_verification: 0,
  }, attention_items: [], monitoring_mode: 'ssh_agentless_near_realtime',
};

const severityMap: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: 'red' }, high: { label: '高危', color: 'orange' },
  medium: { label: '中危', color: 'gold' }, low: { label: '低危', color: 'blue' },
};
const statusLabels: Record<string, string> = {
  open: '待处理', triggered: '已触发', acknowledged: '已确认', in_progress: '处理中',
  pending_verification: '待验证', resolved: '人工登记完成', failed: '失败', unhealthy: '异常', error: '异常',
  healthy: '正常', success: '成功',
};

export function DashboardPreviewPage({ onNavigate, preview = true }: {
  onNavigate: (page: PageKey) => void;
  onViewFindings?: (assetId: string) => void;
  onOpenTerminal?: (assetId: string) => void;
  preview?: boolean;
}) {
  const [snapshot, setSnapshot] = useState(emptySnapshot);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [api, holder] = message.useMessage();
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true); setLoadError('');
    try {
      const next = await getDashboard();
      if (current === requestId.current) setSnapshot(next);
    } catch (error) {
      if (current !== requestId.current) return;
      const text = error instanceof Error ? error.message : '总览数据加载失败';
      setLoadError(text); api.error(text);
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [api]);
  useEffect(() => {
    void load();
    const expire = () => {
      requestId.current += 1; setSnapshot(emptySnapshot);
      setLoadError('会话已失效，请重新登录'); setLoading(false);
    };
    window.addEventListener('sentinel:session-expired', expire);
    return () => { requestId.current += 1; window.removeEventListener('sentinel:session-expired', expire); };
  }, [load]);

  const healthNeedsAttention = snapshot.assets.offline > 0 || snapshot.operations.severe_alerts > 0
    || snapshot.service_health.summary.unavailable > 0;
  return <section className="dashboard-preview-page">{holder}
    <div className="preview-heading">
      <div><h1>综合总览</h1></div>
      <div className="preview-heading-actions">
        <span className={`preview-health-badge ${healthNeedsAttention ? 'is-warning' : 'is-healthy'}`}><i />{healthNeedsAttention ? '需要关注' : '运行正常'}</span>
        <Tooltip title="刷新真实数据"><Button aria-label="刷新总览预览" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip>
        <Button type="primary" icon={<ScanSearch size={17} />} onClick={() => onNavigate('scans')}>发起扫描</Button>
      </div>
    </div>
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}>
      {loadError ? <Alert type="error" showIcon message="总览数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
      <div hidden={Boolean(loadError)} className={`dashboard-preview-data${loading ? ' is-loading' : ''}`}>
        <div className="preview-kpi-grid">
          <Kpi icon={<Server size={19} />} label="服务器" value={snapshot.assets.total} detail={`${snapshot.assets.online} 在线 · ${snapshot.assets.offline} 离线`} tone={snapshot.assets.offline ? 'danger' : 'safe'} />
          <Kpi icon={<Network size={19} />} label="服务" value={snapshot.catalog.services} detail={`${snapshot.service_health.summary.healthy} 个健康 · ${snapshot.service_health.summary.unavailable} 个不可用`} tone={snapshot.service_health.summary.unavailable ? 'danger' : 'safe'} />
          <Kpi icon={<Bell size={19} />} label="活动告警" value={snapshot.operations.active_alerts} detail={`${snapshot.operations.severe_alerts} 个严重 · ${snapshot.operations.active_incidents} 个活动故障`} tone={snapshot.operations.severe_alerts ? 'danger' : 'neutral'} />
          <Kpi icon={<ListChecks size={19} />} label="当前待办" value={snapshot.attention_items.length} detail={`${snapshot.operations.pending_verification} 个待验证`} tone={snapshot.attention_items.length ? 'warning' : 'safe'} />
          <Kpi icon={<ShieldAlert size={19} />} label="待处理漏洞" value={snapshot.vulnerabilities.pending} detail={`${snapshot.vulnerabilities.critical} 严重 · ${snapshot.vulnerabilities.high} 高危`} tone={snapshot.vulnerabilities.pending ? 'danger' : 'safe'} />
          <Kpi icon={<Database size={19} />} label="知识库" value={snapshot.knowledge.freshness} detail={`${snapshot.knowledge.healthy_source_count}/${snapshot.knowledge.source_count} 数据源正常`} tone={snapshot.knowledge.stale ? 'warning' : 'safe'} />
        </div>
        <div className="preview-main-grid">
          <PreviewPanel title="服务运行态势" action={<Button type="link" onClick={() => onNavigate('observability')}>进入服务监控</Button>}>
            <div className="preview-health-summary"><HealthStat label="健康" value={snapshot.service_health.summary.healthy} tone="safe" /><HealthStat label="降级" value={snapshot.service_health.summary.degraded} tone="warning" /><HealthStat label="不可用" value={snapshot.service_health.summary.unavailable} tone="danger" /><HealthStat label="未观测" value={snapshot.service_health.summary.unobserved} tone="muted" /></div>
            {snapshot.service_health.services.length ? <div className="preview-service-list">{snapshot.service_health.services.slice(0, 5).map((item) => <ServiceRow item={item} key={item.service_id} />)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务目录数据" />}
          </PreviewPanel>
          <PreviewPanel title="统一待办" action={<Button type="link" onClick={() => onNavigate('alerts')}>查看全部</Button>}>
            {snapshot.attention_items.length ? <div className="preview-todo-list">{snapshot.attention_items.slice(0, 6).map((item) => <TodoRow item={item} onNavigate={onNavigate} key={`${item.kind}-${item.id}`} />)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待办事项" />}
          </PreviewPanel>
          <PreviewPanel title="CMDB 编目" action={<Button type="link" onClick={() => onNavigate('cmdb')}>进入 CMDB</Button>}>
            <div className="preview-catalog-stats"><CatalogStat label="业务" value={snapshot.catalog.businesses} /><CatalogStat label="应用" value={snapshot.catalog.applications} /><CatalogStat label="服务" value={snapshot.catalog.services} /><CatalogStat label="API" value={snapshot.catalog.apis} /></div>
            <div className="preview-catalog-flow"><span>业务</span><i>→</i><span>应用</span><i>→</i><span>服务</span><i>→</i><span>API</span></div>
            <div className="preview-catalog-link"><strong>{snapshot.catalog.linked_assets}</strong><span>台资产已关联</span><small>{snapshot.catalog.unlinked_assets} 台待编目</small></div>
          </PreviewPanel>
        </div>
        <div className="preview-chart-grid">
          <PreviewPanel title="资源使用趋势"><ResourceChart data={snapshot.resource_trends} /></PreviewPanel>
          <PreviewPanel title="漏洞风险"><RiskChart data={snapshot.vulnerabilities} onNavigate={onNavigate} /></PreviewPanel>
        </div>
        <div className="preview-signal-grid">
          <PreviewPanel title="活动告警" action={<Button type="link" onClick={() => onNavigate('alerts')}>告警中心</Button>}>
            {snapshot.active_alerts.length ? <div className="preview-alert-list">{snapshot.active_alerts.slice(0, 5).map((item) => <div key={`${item.asset_id}-${item.metric}`}><span className="preview-alert-icon"><AlertTriangle size={15} /></span><span><strong>{item.asset_name}</strong><small>{metricLabel(item.metric)} {item.value.toFixed(1)} / 阈值 {item.threshold.toFixed(1)}</small></span><Tag color="red">告警</Tag></div>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无活动告警" />}
          </PreviewPanel>
          <PreviewPanel title="扫描任务" action={<Button type="link" onClick={() => onNavigate('scans')}>任务列表</Button>}>
            {snapshot.recent_scans.length ? <div className="preview-task-list">{snapshot.recent_scans.slice(0, 5).map((item) => <button type="button" key={item.id} onClick={() => onNavigate('scans')}><span><strong>{item.name}</strong><small>{formatTime(item.created_at)} · {item.finding_count} 个漏洞</small></span><Tag color={item.status === 'completed' ? 'green' : item.status === 'failed' ? 'red' : 'blue'}>{scanLabel(item.status)}</Tag></button>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无扫描任务" />}
          </PreviewPanel>
        </div>
      </div>
    </Spin>
  </section>;
}

function PreviewPanel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return <section className="preview-panel"><div className="preview-panel-heading"><strong>{title}</strong>{action}</div>{children}</section>;
}
function Kpi({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: number | string; detail: string; tone: string }) {
  return <div className={`preview-kpi preview-kpi-${tone}`}><span className="preview-kpi-icon">{icon}</span><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></div>;
}
function HealthStat({ label, value, tone }: { label: string; value: number; tone: string }) { return <div className={`preview-health-stat is-${tone}`}><span>{label}</span><strong>{value}</strong></div>; }
function CatalogStat({ label, value }: { label: string; value: number }) { return <div><span>{label}</span><strong>{value}</strong></div>; }
function ServiceRow({ item }: { item: DashboardServiceHealthItem }) {
  const labels = { healthy: '健康', degraded: '降级', unavailable: '不可用', unobserved: '未观测' };
  return <div className="preview-service-row"><span className={`preview-status-dot ${item.status}`} /><span><strong>{item.service_name}</strong><small>{item.business_name} · {item.asset_count} 台资产 · {item.active_alerts} 个告警</small></span><Tag color={item.status === 'healthy' ? 'green' : item.status === 'degraded' ? 'orange' : item.status === 'unavailable' ? 'red' : 'default'}>{labels[item.status]}</Tag></div>;
}
function TodoRow({ item, onNavigate }: { item: DashboardAttentionItem; onNavigate: (page: PageKey) => void }) {
  const target = ['alerts', 'incidents', 'findings', 'scans', 'cmdb', 'assets', 'observability', 'probes', 'automation'].includes(item.target) ? item.target as PageKey : 'dashboard';
  const detail = item.detail.endsWith(` · ${item.status}`)
    ? `${item.detail.slice(0, -item.status.length)}${statusLabels[item.status] ?? item.status}`
    : item.detail;
  return <button type="button" className="preview-todo-row" onClick={() => onNavigate(target)}><span className={`preview-todo-icon is-${item.severity}`}><Activity size={14} /></span><span><strong>{item.title}</strong><small>{detail} · {formatTime(item.occurred_at)}</small></span><Tag color={item.severity === 'critical' ? 'red' : item.severity === 'high' ? 'orange' : 'blue'}>{statusLabels[item.status] ?? item.status}</Tag></button>;
}
function ResourceChart({ data }: { data: DashboardSnapshot['resource_trends'] }) {
  const width = 640; const height = 190; const pad = 24;
  const series = [{ key: 'cpu_percent' as const, label: 'CPU', color: '#176f55' }, { key: 'memory_percent' as const, label: '内存', color: '#347aa3' }, { key: 'disk_percent' as const, label: '磁盘', color: '#c27a2c' }];
  const point = (index: number, value: number) => `${pad + (index * (width - pad * 2)) / Math.max(data.length - 1, 1)},${height - pad - (Math.min(Math.max(value, 0), 100) * (height - pad * 2)) / 100}`;
  return data.length ? <div className="preview-chart"><div className="preview-chart-legend">{series.map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="CPU 内存 磁盘资源趋势图"><line x1={pad} y1={pad} x2={width - pad} y2={pad} /><line x1={pad} y1={height / 2} x2={width - pad} y2={height / 2} /><line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} />{series.map((item) => <polyline key={item.key} points={data.map((row, index) => point(index, row[item.key])).join(' ')} style={{ stroke: item.color }} />)}</svg><div className="preview-chart-scale"><span>100%</span><span>50%</span><span>0%</span></div></div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势样本" />;
}
function RiskChart({ data, onNavigate }: { data: DashboardSnapshot['vulnerabilities']; onNavigate: (page: PageKey) => void }) {
  const items = [['critical', data.critical], ['high', data.high], ['medium', data.medium], ['low', data.low] ] as const;
  const max = Math.max(...items.map(([, value]) => value), 1);
  return <div className="preview-risk-chart"><div className="preview-risk-total"><strong>{data.pending}</strong><span>待处理漏洞</span><small>{data.manual_confirmation} 个需人工确认</small></div><div className="preview-risk-bars">{items.map(([key, count]) => <div key={key}><span className="preview-risk-label"><i style={{ background: severityMap[key].color }} />{severityMap[key].label}</span><div><b style={{ width: `${barWidth(count, max)}%`, background: severityMap[key].color }} /></div><strong>{count}</strong></div>)}</div><Button type="link" icon={<ShieldAlert size={15} />} onClick={() => onNavigate('findings')}>进入漏洞治理</Button></div>;
}
function formatTime(value: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', platformDateTimeOptions({ month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })).format(new Date(value)) : '未采集'; }
function metricLabel(metric: string) { return ({ cpu_percent: 'CPU', memory_percent: '内存', disk_percent: '磁盘', load_1m: '负载' } as Record<string, string>)[metric] ?? metric; }
function scanLabel(status: string) { return ({ queued: '排队', scanning: '扫描中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' } as Record<string, string>)[status] ?? status; }
function barWidth(value: number, max: number) { return value ? Math.max((value / max) * 100, 8) : 0; }
