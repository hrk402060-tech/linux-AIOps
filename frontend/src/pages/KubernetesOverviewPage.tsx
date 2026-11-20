import { formatPlatformDateTime } from '../utils/platformPreferences';
import { useState } from 'react';
import { Alert, Button, Descriptions, Drawer, Empty, Input, Progress, Select, Spin, Table, Tabs, Tag, Tooltip } from 'antd';
import { Activity, Boxes, CircleAlert, Cpu, Database, Eye, HardDrive, Layers, Network, RefreshCw, Server } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import { kubernetesFailureMessage, type KubernetesCluster } from '../api/kubernetes';
import type {
  KubernetesCollectionState, KubernetesOverview, KubernetesResourceItem, KubernetesResourceKind,
} from '../api/kubernetesOverview';
import { useKubernetesOverview } from '../hooks/useKubernetesOverview';
import { useKubernetesResources } from '../hooks/useKubernetesResources';
import './kubernetesOverview.css';

const stateLabels: Record<KubernetesCollectionState, string> = {
  unobserved: '未采集', queued: '等待采集', running: '采集中', complete: '采集完成',
  partial: '部分成功', failed: '采集失败', cancelled: '采集已取消',
};
const resourceLabels: Record<string, string> = {
  nodes: '节点', namespaces: '命名空间', pods: 'Pod', deployments: 'Deployment',
  statefulsets: 'StatefulSet', daemonsets: 'DaemonSet', services: 'Service', events: '事件',
};
const resourceKinds: Array<{ value: KubernetesResourceKind; label: string }> = [
  { value: 'nodes', label: 'Node' }, { value: 'namespaces', label: 'Namespace' },
  { value: 'pods', label: 'Pod' }, { value: 'deployments', label: 'Deployment' },
  { value: 'statefulsets', label: 'StatefulSet' }, { value: 'daemonsets', label: 'DaemonSet' },
  { value: 'services', label: 'Service' }, { value: 'events', label: 'Warning 事件' },
];
const stats: Array<{
  key: keyof KubernetesOverview['counts']; title: string; resource: string;
  icon: LucideIcon;
}> = [
  { key: 'nodes', title: '节点', resource: 'nodes', icon: Server },
  { key: 'namespaces', title: '命名空间', resource: 'namespaces', icon: Layers },
  { key: 'pods', title: 'Pod', resource: 'pods', icon: Boxes },
  { key: 'services', title: 'Service', resource: 'services', icon: Network },
  { key: 'deployments', title: 'Deployment', resource: 'deployments', icon: Activity },
  { key: 'statefulsets', title: 'StatefulSet', resource: 'statefulsets', icon: Database },
  { key: 'daemonsets', title: 'DaemonSet', resource: 'daemonsets', icon: HardDrive },
  { key: 'warning_events', title: 'Warning 事件', resource: 'events', icon: CircleAlert },
];
function date(value: string | null | undefined): string {
  return value && Number.isFinite(Date.parse(value)) ? formatPlatformDateTime(value) : '—';
}
function numeric(value: number | null | undefined, digits = 2): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}
function bytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${(value / 1073741824).toFixed(2)} GiB`;
}
function resourceHint(data: KubernetesOverview | null, resource: string): string {
  const summary = data?.resources[resource];
  if (!summary || summary.state === 'unobserved') return '未采集';
  if (summary.state === 'partial') return `已观测 ${summary.observed_count} 条`;
  return summary.state === 'failed' ? '读取失败' : '完整清单';
}
function Usage({ title, value, detail }: { title: string; value: number | null | undefined; detail: string }) {
  const valid = typeof value === 'number' && Number.isFinite(value);
  const color = valid && value >= 90 ? '#dc2626' : valid && value >= 70 ? '#d97706' : '#0d9488';
  return <div className="k8s-usage">
    <div><Cpu size={17} /><span>{title}</span><strong>{valid ? `${value.toFixed(1)}%` : '—'}</strong></div>
    {valid ? <Progress percent={Math.min(100, value)} strokeColor={color} showInfo={false} size="small" />
      : <div className="k8s-unknown-meter" aria-label={`${title}数据不可用`} />}
    <small>{detail}</small>
  </div>;
}
function resourceStatus(item: KubernetesResourceItem): string {
  if (typeof item.ready === 'boolean') return item.ready ? 'Ready' : 'NotReady';
  for (const key of ['phase', 'reason', 'service_type']) {
    if (typeof item[key] === 'string') return item[key] as string;
  }
  const ready = item.ready_replicas;
  const desired = item.desired_replicas;
  if (typeof ready === 'number' && typeof desired === 'number') return `${ready}/${desired} Ready`;
  return '—';
}
function resourceDetailValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function KubernetesOverviewPage({ currentUser, initialCluster, onOpenConnections }: {
  currentUser: Pick<AuthUser, 'role'>; initialCluster?: KubernetesCluster; onOpenConnections: () => void;
}) {
  const allowed = currentUser.role === 'admin';
  const view = useKubernetesOverview(allowed, initialCluster);
  const [resourceTab, setResourceTab] = useState('summary');
  const [resourceKind, setResourceKind] = useState<KubernetesResourceKind>('nodes');
  const [resourceSearch, setResourceSearch] = useState('');
  const [resourceNamespace, setResourceNamespace] = useState('');
  const [resourceStatusFilter, setResourceStatusFilter] = useState('');
  const resources = useKubernetesResources(
    view.selectedId, resourceKind,
    { search: resourceSearch, namespace: resourceNamespace, status: resourceStatusFilter },
    allowed && resourceTab === 'resources',
  );
  const { data, cluster } = view;
  if (!allowed) return <Alert showIcon type="info" message="当前阶段仅管理员可查看 Kubernetes 集群总览。" />;
  const stale = data?.freshness === 'stale' || Boolean(view.error && data);
  const disabled = cluster && cluster.lifecycle !== 'active';
  const metrics = data?.metrics;
  const warnings = data?.warnings ?? [];
  const counts = data?.counts;
  const unavailableMetrics = !metrics || metrics.state === 'unavailable';
  const state = data?.collection_state ?? 'unobserved';
  const stateColor = ['failed', 'cancelled'].includes(state) ? 'red' : state === 'partial' ? 'gold' : 'default';
  return <section className="k8s-overview" aria-label="Kubernetes 集群总览">
    <div className="page-heading">
      <h1>集群总览</h1>
      <Button type="primary" icon={<RefreshCw size={16} />} aria-label="采集最新数据"
        loading={view.busy} disabled={!cluster || Boolean(disabled) || view.busy || view.collecting || Boolean(view.listError)}
        onClick={() => void view.refresh()}>采集最新数据</Button>
    </div>
    <div className="k8s-controls">
      <Select aria-label="选择 Kubernetes 集群" value={view.selectedId || undefined}
        placeholder="选择集群" loading={view.listLoading} disabled={view.busy}
        options={view.clusters.map((item) => ({ value: item.id,
          label: `${item.name}${item.lifecycle === 'active' ? '' : item.lifecycle === 'disabled' ? '（已停用）' : '（已归档）'}` }))}
        onChange={view.selectCluster} />
      <Input.Search aria-label="搜索总览集群" placeholder="搜索集群" allowClear
        onSearch={view.setSearch} disabled={view.busy} maxLength={100} />
      <Select aria-label="页面自动刷新" value={view.pollSeconds} onChange={view.setPollSeconds}
        options={[{ value: 0, label: '关闭页面刷新' }, { value: 30, label: '页面刷新 30 秒' }, { value: 60, label: '页面刷新 60 秒' }]} />
      {view.nextOffset < view.total ? <Button disabled={view.listLoading || view.busy}
        onClick={() => void view.loadClusters(true, view.nextOffset)}>加载更多集群</Button> : null}
    </div>
    {view.listError ? <Alert showIcon type="error" message={view.listError} action={
      <Button onClick={() => void view.loadClusters()}>重试</Button>} /> : null}
    {view.error ? <Alert showIcon type="error" message={view.error} /> : null}
    {view.notice ? <Alert showIcon type="info" message={view.notice} /> : null}
    {view.listLoading && !view.clusters.length ? <div className="k8s-loading"><Spin /><span>正在加载集群</span></div> : null}
    {!view.listLoading && !view.listError && !view.clusters.length
      ? <Empty description={view.search ? '暂无匹配集群' : '暂无已接入集群'}>
        <Button onClick={onOpenConnections}>接入集群</Button>
      </Empty> : null}
    {view.selectedId ? <>
      <div className="k8s-statusline">
        <strong>{cluster?.name ?? '集群'}</strong><span>{cluster?.version ?? '版本未知'}</span>
        <Tag color={stateColor}>{stateLabels[state]}</Tag>
        {disabled ? <Tag>{cluster.lifecycle === 'archived' ? '已归档' : '已停用'}</Tag> : null}
        {stale ? <Tag color="gold">数据已过期</Tag> : null}
        {data?.freshness === 'unavailable' ? <Tag color="red">数据不可用</Tag> : null}
        {view.loading ? <Spin size="small" /> : null}
      </div>
      <div className="k8s-statusline k8s-times">
        <span>采集时间 {date(data?.observed_at)}</span><span>最近成功 {date(data?.last_success_at)}</span>
        <span>采集周期</span>
        <Select aria-label="集群采集周期" value={cluster?.collection_interval_seconds ?? 0}
          disabled={view.busy || !cluster || Boolean(disabled)} onChange={view.setCollectionInterval}
          options={[{ value: 0, label: '手动采集' }, { value: 60, label: '60 秒' }, { value: 300, label: '5 分钟' }]} />
      </div>
      <div className="k8s-stats">
        {stats.map(({ key, title, resource, icon: Icon }) => <div className="k8s-stat" key={key}>
          <div><Icon size={17} /><span>{title}</span></div>
          <strong data-testid={`k8s-count-${key}`}>{counts?.[key] ?? '—'}</strong>
          <small>{resourceHint(data, resource)}</small>
        </div>)}
      </div>
      <div className="k8s-condition-row">
        <span>节点就绪 <strong>{counts?.nodes_ready ?? '—'}</strong></span>
        <span className={counts?.nodes_not_ready ? 'k8s-danger' : ''}>节点异常 <strong>{counts?.nodes_not_ready ?? '—'}</strong></span>
        <span className={counts?.pods_abnormal ? 'k8s-danger' : ''}>异常 Pod <strong>{counts?.pods_abnormal ?? '—'}</strong></span>
      </div>
      <section className="k8s-resources" aria-label="集群资源使用">
        <div className="k8s-section-heading"><h2>资源使用</h2>
          <Tag color={unavailableMetrics ? 'default' : metrics?.state === 'partial' ? 'gold' : 'green'}>
            {unavailableMetrics ? '指标不可用' : metrics?.state === 'partial' ? '部分覆盖' : '已采集'}
          </Tag>
        </div>
        <div className="k8s-usage-grid">
          <Usage title="CPU 使用率" value={metrics?.cpu_percent}
            detail={`${numeric(metrics?.cpu_cores)} / ${numeric(metrics?.cpu_allocatable_cores)} 核`} />
          <Usage title="内存使用率" value={metrics?.memory_percent}
            detail={`${bytes(metrics?.memory_bytes)} / ${bytes(metrics?.memory_allocatable_bytes)}`} />
        </div>
        <div className="k8s-statusline k8s-times">
          <span>{metrics?.source ?? 'metrics.k8s.io'}</span>
          <Tooltip title="参与用量和可分配资源汇总的同一组有效节点"><span>
            {metrics?.covered_nodes ?? 0} / {metrics?.total_nodes ?? '—'} 个节点
          </span></Tooltip>
          <span>样本时间 {date(metrics?.sample_time)}</span><span>采样窗口 {metrics?.window ?? '—'}</span>
        </div>
      </section>
      {data?.errors.length ? <div className="k8s-error-list" aria-label="采集异常">
        {data.errors.map((item, index) => <Alert key={`${item.resource}-${index}`} type="warning" showIcon
          message={`${resourceLabels[item.resource] ?? '集群'}：${kubernetesFailureMessage(item.code, '本次读取未完成')}`} />)}
      </div> : null}
      <section className="k8s-events" aria-label="集群 Warning 事件">
        <div className="k8s-section-heading"><h2>Warning 事件</h2>
          <span>{counts?.warning_events ?? '—'} 个事件对象</span></div>
        <div className="table-frame"><Table dataSource={warnings} size="small" loading={view.loading && !data}
          rowKey={(item) => `${item.namespace}/${item.target}/${item.reason}/${item.last_seen}`}
          scroll={{ x: 650 }} pagination={false} columns={[
            { title: '原因', dataIndex: 'reason', width: 180 },
            { title: '对象', dataIndex: 'target', render: (value: string) => <span className="k8s-object">{value}</span> },
            { title: '命名空间', dataIndex: 'namespace', render: (value: string | null) => value ?? '—' },
            { title: '累计次数', dataIndex: 'count', render: (value: number | null) => value ?? '—' },
            { title: '最近发生', dataIndex: 'last_seen', render: date },
          ]} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
            data?.resources.events?.state === 'complete' ? '暂无 Warning 事件' : '尚无完整事件数据'
          } /> }} /></div>
      </section>
      <section className="k8s-resource-browser" aria-label="集群资源清单">
        <div className="k8s-section-heading">
          <h2>资源清单</h2>
          <Tabs activeKey={resourceTab} onChange={setResourceTab} items={[
            { key: 'summary', label: '概览' },
            { key: 'resources', label: '只读查看' },
          ]} />
        </div>
        {resourceTab === 'resources' ? <>
          <div className="k8s-resource-controls">
            <Select aria-label="资源类型" value={resourceKind} options={resourceKinds}
              onChange={(value: KubernetesResourceKind) => {
                setResourceKind(value); setResourceStatusFilter('');
              }} />
            <Input.Search aria-label="搜索资源" placeholder="搜索名称或 UID" allowClear
              value={resourceSearch} onChange={(event) => setResourceSearch(event.target.value)}
              maxLength={100} />
            <Input aria-label="命名空间筛选" placeholder="命名空间" allowClear
              value={resourceNamespace} onChange={(event) => setResourceNamespace(event.target.value)}
              maxLength={253} />
            <Select aria-label="资源状态" value={resourceStatusFilter || undefined} allowClear
              placeholder="状态筛选" onChange={(value) => setResourceStatusFilter(value)}
              options={[
                { value: 'Ready', label: 'Ready' }, { value: 'NotReady', label: 'NotReady' },
                { value: 'Running', label: 'Running' }, { value: 'Pending', label: 'Pending' },
                { value: 'Failed', label: 'Failed' }, { value: 'Active', label: 'Active' },
              ]} />
          </div>
          {resources.error ? <Alert showIcon type="error" message={resources.error} /> : null}
          {resources.data?.partial ? <Alert showIcon type="warning"
            message="当前清单为部分采集结果，数量未知，不能据此判断集群没有其他资源。" /> : null}
          <div className="table-frame">
            <Table<KubernetesResourceItem> dataSource={resources.items} loading={resources.loading}
              rowKey="uid" scroll={{ x: 760 }} pagination={false} columns={[
                { title: '名称', dataIndex: 'name', render: (value: string) =>
                  <span className="k8s-object">{value}</span> },
                { title: '命名空间', dataIndex: 'namespace', render: (value: string | null) => value ?? '—' },
                { title: '状态', render: (_, item) => resourceStatus(item) },
                { title: '重启次数', dataIndex: 'restarts', render: (value: number | undefined) => value ?? '—' },
                { title: '创建时间', dataIndex: 'created_at', render: date },
                { title: '操作', fixed: 'right', width: 92, render: (_, item) =>
                  <Button type="link" icon={<Eye size={15} />} onClick={() => resources.openDetail(item.uid)}>
                    查看
                  </Button> },
              ]} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={resources.loading ? '正在加载资源' : '暂无资源快照'} /> }} />
          </div>
          {resources.data?.total !== null && resources.data?.total !== undefined
            ? <div className="k8s-resource-total">共 {resources.data.total} 条</div> : null}
          <Drawer title={`${resourceKinds.find((item) => item.value === resourceKind)?.label ?? '资源'}详情`}
            open={Boolean(resources.detail) || resources.detailLoading} onClose={resources.clearDetail}
            loading={resources.detailLoading} width={520}>
            {resources.detailError ? <Alert showIcon type="error" message={resources.detailError} /> : null}
            {resources.detail?.item ? <Descriptions column={1} size="small" bordered>
              {Object.entries(resources.detail.item).map(([key, value]) =>
                <Descriptions.Item key={key} label={key}>{resourceDetailValue(value)}</Descriptions.Item>)}
            </Descriptions> : null}
          </Drawer>
        </> : <div className="k8s-resource-summary">
          <span>在当前采集快照中按资源类型查看只读对象。</span>
          <Button type="link" onClick={() => setResourceTab('resources')}>打开资源清单</Button>
        </div>}
      </section>
    </> : null}
  </section>;
}
