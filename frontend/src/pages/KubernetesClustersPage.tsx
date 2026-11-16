import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Select, Space, Table, Tag, Tooltip } from 'antd';
import { CheckCircle2, Edit3, KeyRound, Plus, RefreshCw, ShieldOff, TestTube2 } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import { ApiError } from '../api/client';
import {
  createKubernetesCluster, getKubernetesClusters, kubernetesFailureMessage, kubernetesRequestError,
  rotateKubernetesClusterCredential, testKubernetesCluster, updateKubernetesCluster,
  type KubernetesCluster, type KubernetesClusterLifecycle, type KubernetesClusterTestResult,
} from '../api/kubernetes';
import {
  ConnectionTestResult, KubernetesActionDialog, KubernetesConnectionForm, KubernetesMetadataForm,
} from './KubernetesClusterForms';

const PAGE_SIZE = 50;
const lifecycleLabels: Record<KubernetesClusterLifecycle, string> = {
  active: '已启用', disabled: '已停用', archived: '已归档',
};
const connectionLabels: Record<string, string> = {
  connected: '已连接', success: '已连接', testing: '检测中', failed: '连接失败',
  unknown: '未检测', untested: '未检测', unavailable: '不可用',
};
const authorizationLabels: Record<string, string> = {
  authorized: '已授权', allowed: '已授权', verified: '已授权', pending: '待授权', forbidden: '授权受限',
  failed: '授权失败', unknown: '未检测', untested: '未检测',
};
const statusOptions = [
  { label: '全部状态', value: '' }, ...Object.entries(lifecycleLabels).map(([value, label]) => ({ label, value })),
];
type ConnectionEditor = { cluster: KubernetesCluster | null };

function statusTag(value: string, labels: Record<string, string>) {
  const color = ['connected', 'success', 'authorized', 'allowed', 'verified'].includes(value)
    ? 'green' : ['failed', 'forbidden', 'unavailable'].includes(value) ? 'red' : 'default';
  return <Tag color={color}>{labels[value] ?? '未知'}</Tag>;
}
function formatDate(value: string | null): string {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : '—';
}
function certificateCell(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return <span>—</span>;
  const days = Math.ceil((Date.parse(value) - Date.now()) / 86_400_000);
  return <Tooltip title={formatDate(value)}><Tag color={days <= 0 ? 'red' : days <= 30 ? 'gold' : 'green'}>
    {days <= 0 ? '已过期' : `${days} 天后到期`}
  </Tag></Tooltip>;
}

export function KubernetesClustersPage({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [items, setItems] = useState<KubernetesCluster[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<KubernetesClusterLifecycle | ''>('');
  const [loading, setLoading] = useState(false);
  const [authoritative, setAuthoritative] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<ConnectionEditor | null>(null);
  const [editing, setEditing] = useState<KubernetesCluster | null>(null);
  const [stopping, setStopping] = useState<KubernetesCluster | null>(null);
  const [testing, setTesting] = useState<KubernetesCluster | null>(null);
  const [testResult, setTestResult] = useState<KubernetesClusterTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  const operationId = useRef(0);
  const mutationPending = useRef(false);
  const writable = currentUser.role === 'admin';
  const allowed = useRef(writable);
  allowed.current = writable;

  const load = useCallback(async () => {
    if (!allowed.current) return;
    const id = ++requestId.current;
    setLoading(true); setAuthoritative(false); setLoadError('');
    try {
      const result = await getKubernetesClusters({ offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE, search, status: status || undefined });
      if (id !== requestId.current || !allowed.current) return;
      const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
      if (page > lastPage) { setPage(lastPage); return; }
      setItems(result.items); setTotal(result.total); setAuthoritative(true);
    } catch (failure) {
      if (id === requestId.current && allowed.current) setLoadError(kubernetesRequestError(failure));
    } finally {
      if (id === requestId.current && allowed.current) setLoading(false);
    }
  }, [page, search, status]);
  useEffect(() => {
    if (writable) void load();
    else { setItems([]); setTotal(0); setEditor(null); setEditing(null); setStopping(null); setTesting(null); setTestResult(null); }
    return () => { requestId.current += 1; };
  }, [load, writable]);
  useEffect(() => () => { operationId.current += 1; }, [writable]);

  const mutate = async (operation: () => Promise<unknown>, success: string) => {
    if (!allowed.current || mutationPending.current || !authoritative) return;
    mutationPending.current = true;
    const id = ++operationId.current;
    requestId.current += 1; setBusy(true); setError(''); setNotice(''); setTestResult(null);
    try {
      await operation();
      if (id !== operationId.current || !allowed.current) return;
      setNotice(success); setEditor(null); setEditing(null); setStopping(null);
      await load();
    } catch (failure) {
      if (id !== operationId.current || !allowed.current) return;
      setError(kubernetesRequestError(failure));
      if (failure instanceof ApiError && [403, 409].includes(failure.status)) {
        setEditor(null); setEditing(null); setStopping(null);
      }
      await load();
    } finally {
      mutationPending.current = false;
      if (id === operationId.current && allowed.current) setBusy(false);
    }
  };
  const runSavedTest = async (reason: string) => {
    if (!testing || mutationPending.current || !allowed.current || !authoritative) return;
    mutationPending.current = true;
    const id = ++operationId.current;
    setBusy(true); setError(''); setNotice(''); setTestResult(null);
    try {
      const result = await testKubernetesCluster(testing.id, { reason });
      if (id !== operationId.current || !allowed.current) return;
      setTestResult(result);
      if (result.status === 'passed') setNotice('连接测试通过');
      else setError(kubernetesFailureMessage(result.error_code, '连接测试未通过，请检查各项结果'));
      await load();
    } catch (failure) {
      if (id === operationId.current && allowed.current) setError(kubernetesRequestError(failure));
    } finally {
      mutationPending.current = false;
      if (id === operationId.current && allowed.current) { setBusy(false); setTesting(null); }
    }
  };
  const unavailable = loading || busy || !authoritative;
  const columns = [
    { title: '集群', width: 180, render: (_: unknown, item: KubernetesCluster) => <div>
      <strong>{item.name}</strong><div>{item.context || '—'}</div><Tag>{lifecycleLabels[item.lifecycle]}</Tag>
    </div> },
    { title: 'API Server', width: 230, render: (_: unknown, item: KubernetesCluster) => <code style={{ overflowWrap: 'anywhere' }}>{item.endpoint || '—'}</code> },
    { title: '版本', width: 110, render: (_: unknown, item: KubernetesCluster) => item.version || '—' },
    { title: '连接', width: 115, render: (_: unknown, item: KubernetesCluster) => statusTag(item.connection_status, connectionLabels) },
    { title: '授权', width: 115, render: (_: unknown, item: KubernetesCluster) => statusTag(item.authorization_status, authorizationLabels) },
    { title: '证书到期', width: 190, render: (_: unknown, item: KubernetesCluster) => <Space direction="vertical" size={2}>
      <span>服务端 {certificateCell(item.server_cert_expires_at)}</span><span>CA {certificateCell(item.ca_cert_expires_at)}</span>
      <span>客户端 {certificateCell(item.client_cert_expires_at)}</span>
    </Space> },
    { title: '最近检测', width: 180, render: (_: unknown, item: KubernetesCluster) => <div>
      <div>{formatDate(item.last_tested_at)}</div><span>成功 {formatDate(item.last_success_at)}</span>
    </div> },
    { title: '失败原因', width: 230, render: (_: unknown, item: KubernetesCluster) => item.error_code || item.error_message
      ? <span>{kubernetesFailureMessage(item.error_code, '检测未通过，请重新测试连接')}</span> : '—' },
    { title: '操作', width: 180, fixed: 'right' as const, render: (_: unknown, item: KubernetesCluster) => <Space size={4}>
      <Tooltip title="编辑集群"><Button aria-label={`编辑 ${item.name}`} icon={<Edit3 size={15} />} disabled={unavailable} onClick={() => setEditing(item)} /></Tooltip>
      <Tooltip title="轮换凭据"><Button aria-label={`轮换 ${item.name} 凭据`} icon={<KeyRound size={15} />} disabled={unavailable || item.lifecycle === 'archived'} onClick={() => setEditor({ cluster: item })} /></Tooltip>
      <Tooltip title="测试连接"><Button aria-label={`测试 ${item.name}`} icon={<TestTube2 size={15} />} disabled={unavailable || item.lifecycle !== 'active'} onClick={() => setTesting(item)} /></Tooltip>
      <Tooltip title={item.lifecycle === 'disabled' ? '重新启用' : '停用集群'}><Button
        aria-label={`${item.lifecycle === 'disabled' ? '启用' : '停用'} ${item.name}`}
        icon={item.lifecycle === 'disabled' ? <CheckCircle2 size={15} /> : <ShieldOff size={15} />}
        disabled={unavailable || item.lifecycle === 'archived'} onClick={() => setStopping(item)} /></Tooltip>
    </Space> },
  ];
  if (!writable) return <section aria-label="Kubernetes 集群接入"><div className="page-heading"><h1>集群接入</h1></div>
    <Alert type="info" showIcon message="当前阶段仅管理员可查看和管理 Kubernetes 集群接入。" /></section>;
  return <section className="kubernetes-page" aria-label="Kubernetes 集群接入">
    <div className="page-heading"><h1>集群接入</h1>
      <Button type="primary" icon={<Plus size={16} />} disabled={unavailable} onClick={() => setEditor({ cluster: null })}>接入集群</Button>
    </div>
    {loadError ? <Alert type="error" showIcon message={loadError} /> : null}
    {error ? <Alert type="error" showIcon message={error} closable onClose={() => setError('')} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} closable onClose={() => setNotice('')} /> : null}
    {testResult ? <ConnectionTestResult result={testResult} /> : null}
    <Space wrap style={{ margin: '16px 0', maxWidth: '100%' }}>
      <Input.Search aria-label="搜索 Kubernetes 集群" placeholder="搜索名称或 API Server" value={searchInput} disabled={busy}
        onChange={(event) => setSearchInput(event.target.value)} onSearch={(value) => { setPage(1); setSearch(value.trim()); }} allowClear
        style={{ width: 'min(320px, 100%)', maxWidth: '100%' }} maxLength={100} />
      <Select aria-label="筛选集群状态" value={status} options={statusOptions} disabled={busy}
        onChange={(value: KubernetesClusterLifecycle | '') => { setPage(1); setStatus(value); }} style={{ width: 150 }} />
      <Tooltip title="刷新集群接入列表"><Button aria-label="刷新集群接入列表" icon={<RefreshCw size={16} />} disabled={loading || busy} onClick={() => void load()} /></Tooltip>
      <span>共 {authoritative ? total : '—'} 个集群</span>
    </Space>
    <div className="table-frame"><Table<KubernetesCluster> rowKey="id" loading={loading} dataSource={items} columns={columns}
      scroll={{ x: 1600 }} pagination={{ current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false, disabled: busy, onChange: setPage }}
      locale={{ emptyText: <Empty description={loadError ? '列表加载失败' : search || status ? '暂无符合条件的集群' : '暂无已接入集群'} /> }} /></div>
    {editor ? <KubernetesConnectionForm key={editor.cluster?.id ?? 'create'} cluster={editor.cluster} busy={busy}
      onCancel={() => setEditor(null)} onCreate={(payload) => mutate(() => createKubernetesCluster(payload), 'Kubernetes 集群已接入')}
      onRotate={(payload) => mutate(() => rotateKubernetesClusterCredential(editor.cluster!.id, payload), 'Kubernetes 凭据已轮换')} /> : null}
    {editing ? <KubernetesMetadataForm key={editing.id} cluster={editing} busy={busy} onCancel={() => setEditing(null)}
      onSubmit={(payload) => mutate(() => updateKubernetesCluster(editing.id, payload), 'Kubernetes 集群配置已更新')} /> : null}
    {stopping ? <KubernetesActionDialog title={`${stopping.lifecycle === 'disabled' ? '启用' : '停用'} ${stopping.name}`}
      action={stopping.lifecycle === 'disabled' ? '重新启用' : '确认停用'} busy={busy} onCancel={() => setStopping(null)}
      onSubmit={(reason) => mutate(() => updateKubernetesCluster(stopping.id, {
        lifecycle: stopping.lifecycle === 'disabled' ? 'active' : 'disabled', expected_revision: stopping.revision, reason,
      }), stopping.lifecycle === 'disabled' ? '集群已重新启用' : '集群已停用')} /> : null}
    {testing ? <KubernetesActionDialog title={`测试 ${testing.name} 的连接`} action="开始测试" busy={busy}
      onCancel={() => setTesting(null)} onSubmit={runSavedTest} /> : null}
  </section>;
}
