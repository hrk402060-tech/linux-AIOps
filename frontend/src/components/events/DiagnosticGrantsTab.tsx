import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, List, Modal, Segmented, Select, Space, Table, Tag, Tooltip } from 'antd';
import { Eye, KeyRound, Plus, RefreshCw, ShieldOff, TestTube2 } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import { getAssets } from '../../api/assets';
import {
  DiagnosticApiError, getDiagnosticGrantAudit, getDiagnosticGrants, revokeDiagnosticGrant, saveDiagnosticGrant,
  testDiagnosticGrant,
  type DiagnosticGrant, type DiagnosticGrantAudit, type DiagnosticGrantInput, type DiagnosticGrantList,
  type DiagnosticGrantStatus,
} from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { DiagnosticGrantForm } from './DiagnosticGrantForm';
import { KubernetesGrantForm } from './KubernetesGrantForm';
import { DockerGrantForm } from './DockerGrantForm';
import { MySQLGrantForm } from './MySQLGrantForm';
import { RedisGrantForm } from './RedisGrantForm';
import { RabbitMQGrantForm } from './RabbitMQGrantForm';

const labels: Record<DiagnosticGrantStatus, string> = {
  active: '已生效', expired: '已过期', disabled: '已撤销', asset_disabled: '资产停用',
  target_changed: '连接目标已变更', host_key_changed: '主机指纹已变更',
};
const emptyList: DiagnosticGrantList = { total: 0, items: [], catalog: [] };
const failureText = (error: unknown) => error instanceof Error ? error.message : '只读诊断授权请求失败';

export function DiagnosticGrantsTab({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [data, setData] = useState(emptyList);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [page, setPage] = useState(1);
  const [assetFilter, setAssetFilter] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [authoritative, setAuthoritative] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [formKind, setFormKind] = useState<'linux' | 'kubernetes' | 'docker' | 'mysql' | 'redis' | 'rabbitmq'>('linux');
  const [editing, setEditing] = useState<DiagnosticGrant | null>(null);
  const [revoking, setRevoking] = useState<DiagnosticGrant | null>(null);
  const [testing, setTesting] = useState<DiagnosticGrant | null>(null);
  const [audit, setAudit] = useState<{ grant: DiagnosticGrant; items: DiagnosticGrantAudit[] } | null>(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditError, setAuditError] = useState('');
  const generation = useRef(0);
  const auditGeneration = useRef(0);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const writable = currentUser.role === 'admin';
  const load = async () => {
    const requestId = ++generation.current;
    setLoading(true); setAuthoritative(false);
    try {
      const [next, availableAssets] = await Promise.all([getDiagnosticGrants((page - 1) * 50, assetFilter), getAssets('all')]);
      if (requestId !== generation.current || !mounted.current) return;
      const lastPage = Math.max(1, Math.ceil(next.total / 50));
      if (page > lastPage) { setPage(lastPage); return; }
      setData(next); setAssets(availableAssets); setAuthoritative(true);
    } catch (failure) {
      if (requestId === generation.current && mounted.current) setError(failureText(failure));
    } finally { if (requestId === generation.current && mounted.current) setLoading(false); }
  };
  useEffect(() => { mounted.current = true; void load(); return () => { generation.current += 1; }; }, [page, assetFilter]);
  useEffect(() => () => { mounted.current = false; auditGeneration.current += 1; }, []);
  useEffect(() => { if (!writable) { setFormOpen(false); setEditing(null); setRevoking(null); } }, [writable]);
  const openForm = (grant: DiagnosticGrant | null) => {
    setEditing(grant); setFormKind(grant?.kind ?? 'linux'); setError(''); setNotice(''); setFormOpen(true);
  };
  const mutate = async (operation: () => Promise<DiagnosticGrant>) => {
    if (inFlight.current || !writable || !authoritative) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    generation.current += 1;
    try {
      await operation();
      if (!mounted.current) return;
      setFormOpen(false); setEditing(null); setRevoking(null); await load();
    } catch (failure) {
      if (!mounted.current) return;
      setError(failureText(failure));
      if (failure instanceof DiagnosticApiError && [403, 409].includes(failure.status)) {
        setFormOpen(false); setEditing(null); setRevoking(null);
      }
      await load();
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  const showAudit = async (grant: DiagnosticGrant) => {
    const requestId = ++auditGeneration.current;
    setAudit({ grant, items: [] }); setAuditLoading(true); setAuditError('');
    try {
      const result = await getDiagnosticGrantAudit(grant.asset_id);
      if (requestId === auditGeneration.current && mounted.current) setAudit({ grant, items: result.items });
    } catch (failure) {
      if (requestId === auditGeneration.current && mounted.current) setAuditError(failureText(failure));
    } finally { if (requestId === auditGeneration.current && mounted.current) setAuditLoading(false); }
  };
  const unavailable = loading || busy || !authoritative;
  const kindSelector = !editing ? <Segmented aria-label="授权类型" value={formKind} disabled={busy}
    style={{ marginBottom: 16, maxWidth: '100%', overflowX: 'auto' }} options={[{ label: 'Linux', value: 'linux' }, { label: 'Kubernetes', value: 'kubernetes' }, { label: 'Docker', value: 'docker' }, { label: 'MySQL', value: 'mysql' }, { label: 'Redis', value: 'redis' }, { label: 'RabbitMQ', value: 'rabbitmq' }]}
    onChange={(value) => setFormKind(value as 'linux' | 'kubernetes' | 'docker' | 'mysql' | 'redis' | 'rabbitmq')} /> : undefined;
  const formAssets = assets.filter((asset) => !data.items.some((grant) => grant.asset_id === asset.id && (grant.kind ?? 'linux') !== formKind));
  const formProps = { assets: formAssets, catalog: data.catalog, initialGrant: editing, busy,
    canSubmit: authoritative && writable, kindSelector, onCancel: () => { setFormOpen(false); setEditing(null); },
    onSubmit: (payload: DiagnosticGrantInput) => void mutate(() => saveDiagnosticGrant(payload)) };
  return <section aria-label="只读诊断授权">
    {error ? <Alert type="error" showIcon message={error} closable onClose={() => setError('')} style={{ marginTop: 12 }} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} closable onClose={() => setNotice('')} style={{ marginTop: 12 }} /> : null}
    <Space wrap style={{ margin: '16px 0' }}>
      <Select aria-label="筛选授权服务器" placeholder="全部服务器" allowClear showSearch optionFilterProp="label"
        value={assetFilter} disabled={busy} style={{ width: 280 }} options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))}
        onChange={(value?: string) => { setError(''); setPage(1); setAssetFilter(value); }} />
      <Tooltip title="刷新只读授权"><Button aria-label="刷新只读授权" icon={<RefreshCw size={16} />} disabled={busy} onClick={() => { setError(''); void load(); }} /></Tooltip>
      {writable ? <Button icon={<Plus size={16} />} type="primary" disabled={unavailable} onClick={() => openForm(null)}>新增只读授权</Button> : null}
    </Space>
    <div className="table-frame"><Table<DiagnosticGrant> rowKey="asset_id" loading={loading} dataSource={data.items}
      scroll={{ x: 1500 }} locale={{ emptyText: <Empty description="暂无只读诊断授权" /> }}
      pagination={{ current: page, total: data.total, pageSize: 50, showSizeChanger: false, disabled: busy, onChange: setPage }} columns={[
        { title: '服务器 / 类型', width: 210, render: (_, item) => <div><strong>{item.asset_name}</strong>
          <div><Tag>{item.kind === 'kubernetes' ? 'Kubernetes' : item.kind === 'docker' ? 'Docker' : item.kind === 'mysql' ? 'MySQL' : item.kind === 'redis' ? 'Redis' : item.kind === 'rabbitmq' ? 'RabbitMQ' : 'Linux'}</Tag></div>
          <div style={{ overflowWrap: 'anywhere' }}>{item.kind === 'kubernetes' ? item.kubernetes_config?.endpoint : item.kind === 'docker' ? item.docker_config?.endpoint : item.kind === 'mysql' ? `${item.mysql_config?.host}:${item.mysql_config?.port}` : item.kind === 'redis' ? `${item.redis_config?.host}:${item.redis_config?.port}` : item.kind === 'rabbitmq' ? `https://${item.rabbitmq_config?.host.includes(':') ? `[${item.rabbitmq_config.host}]` : item.rabbitmq_config?.host}:${item.rabbitmq_config?.port}` : `${item.host}:${item.port}`}</div></div> },
        { title: '专用身份', width: 200, render: (_, item) => <span style={{ overflowWrap: 'anywhere' }}>
          {item.kind === 'kubernetes' ? `${item.kubernetes_config?.service_account_namespace ?? ''} / ${item.kubernetes_config?.service_account_name ?? ''}` : item.kind === 'docker' ? item.docker_config?.proxy_identity : item.kind === 'mysql' ? item.mysql_config?.username : item.kind === 'redis' ? item.redis_config?.username : item.kind === 'rabbitmq' ? item.rabbitmq_config?.username : item.username}</span> },
        { title: '采集范围', width: 240, render: (_, item) => <div>{item.kind === 'kubernetes'
          ? <div style={{ overflowWrap: 'anywhere' }}>命名空间：{item.kubernetes_config?.namespaces.join('、')}</div> : null}
          {item.kind === 'rabbitmq' ? <div style={{ overflowWrap: 'anywhere' }}>vhost：{item.rabbitmq_config?.vhost}</div> : null}
          {item.action_ids.map((id) => <Tag key={id}>{data.catalog.find((action) => action.id === id)?.label ?? id}</Tag>)}</div> },
        { title: '状态 / 版本', width: 155, render: (_, item) => <Space direction="vertical" size={2}><Tag color={item.effective_status === 'active' ? 'green' : 'gold'}>{labels[item.effective_status] ?? item.effective_status}</Tag><span>版本 {item.revision}</span></Space> },
        { title: '有效期', width: 175, dataIndex: 'expires_at', render: (value: string) => new Date(value).toLocaleString() },
        { title: '凭据与指纹', width: 300, render: (_, item) => item.kind === 'kubernetes'
          ? <div><div>Token：{item.secret_configured ? '********' : '未配置'}</div>
            <div>CA：<TypographyFingerprint value={item.ca_fingerprint ?? '未配置'} /></div></div>
          : item.kind === 'docker'
            ? <div><div>Token：{item.secret_configured ? '********' : '未配置'}</div><div>CA：<TypographyFingerprint value={item.ca_fingerprint ?? '未配置'} /></div></div>
          : item.kind === 'mysql'
            ? <div><div>密码：{item.secret_configured ? '********' : '未配置'}</div><div>CA：<TypographyFingerprint value={item.ca_fingerprint ?? '未配置'} /></div></div>
          : item.kind === 'redis' || item.kind === 'rabbitmq'
            ? <div><div>密码：{item.secret_configured ? '********' : '未配置'}</div><div>CA：<TypographyFingerprint value={item.ca_fingerprint ?? '未配置'} /></div></div>
          : <div><div>私钥：{item.secret_configured ? '********' : '未配置'}</div><div>密钥：<TypographyFingerprint value={item.key_fingerprint} /></div><div>主机：<TypographyFingerprint value={item.host_fingerprint} /></div></div> },
        { title: '更新记录', width: 160, render: (_, item) => <div>{item.operator}<div>{new Date(item.updated_at).toLocaleString()}</div></div> },
        { title: '操作', width: writable ? 132 : 60, fixed: 'right', render: (_, item) => <Space size={4}>
          <Tooltip title="查看授权记录"><Button aria-label={`查看 ${item.asset_name} 授权记录`} icon={<Eye size={16} />} onClick={() => void showAudit(item)} /></Tooltip>
          {writable ? <><Tooltip title="验证只读授权"><Button aria-label={`验证 ${item.asset_name} 只读授权`} disabled={unavailable || !item.enabled} icon={<TestTube2 size={16} />} onClick={() => { setError(''); setTesting(item); }} /></Tooltip><Tooltip title="配置只读授权"><Button aria-label={`配置 ${item.asset_name} 只读授权`} disabled={unavailable || !assets.some((asset) => asset.id === item.asset_id && asset.lifecycle_status === 'enabled')} icon={<KeyRound size={16} />} onClick={() => openForm(item)} /></Tooltip>
            <Tooltip title="撤销只读授权"><Button aria-label={`撤销 ${item.asset_name} 只读授权`} disabled={unavailable || !item.enabled} icon={<ShieldOff size={16} />} onClick={() => { setError(''); setRevoking(item); }} /></Tooltip></> : null}
        </Space> },
      ]} /></div>
    {formOpen ? formKind === 'kubernetes'
      ? <KubernetesGrantForm key="kubernetes" {...formProps} />
      : formKind === 'docker' ? <DockerGrantForm key="docker" {...formProps} />
      : formKind === 'mysql' ? <MySQLGrantForm key="mysql" {...formProps} />
      : formKind === 'redis' ? <RedisGrantForm key="redis" {...formProps} />
      : formKind === 'rabbitmq' ? <RabbitMQGrantForm key="rabbitmq" {...formProps} />
      : <DiagnosticGrantForm key="linux" {...formProps} /> : null}
    {revoking ? <RevokeDialog grant={revoking} busy={busy} onCancel={() => setRevoking(null)}
      onRevoke={(confirmation, reason) => void mutate(() => revokeDiagnosticGrant(revoking.asset_id, { expected_revision: revoking.revision, confirmation, reason }))} /> : null}
    {testing ? <TestDialog grant={testing} onCancel={() => setTesting(null)}
      onTest={async (confirmation, reason) => {
        try {
          const result = await testDiagnosticGrant(testing.asset_id, {
            expected_revision: testing.revision, confirmation, reason,
          });
           if (result.status === 'completed') setNotice(`授权验证通过：${result.summary}`);
          else setError(`授权验证未完成：${result.summary}`);
        } catch (failure) {
          setError(failureText(failure));
        } finally { setTesting(null); }
      }} /> : null}
    <Modal title={audit ? `${audit.grant.asset_name} · 授权记录` : '授权记录'} open={audit !== null} footer={null}
      onCancel={() => { auditGeneration.current += 1; setAudit(null); }} destroyOnHidden>
      {auditError ? <Alert type="error" showIcon message={auditError} /> : <List loading={auditLoading} dataSource={audit?.items ?? []}
        locale={{ emptyText: '暂无授权记录' }} renderItem={(item) => <List.Item><List.Item.Meta title={`${item.action} · 版本 ${item.revision}`}
          description={<>{item.reason}<div>{item.operator} · {new Date(item.created_at).toLocaleString()}</div></>} /></List.Item>} />}
    </Modal>
  </section>;
}
function TypographyFingerprint({ value }: { value: string }) {
  return <code style={{ overflowWrap: 'anywhere' }}>{value}</code>;
}
function RevokeDialog({ grant, busy, onCancel, onRevoke }: {
  grant: DiagnosticGrant; busy: boolean; onCancel: () => void; onRevoke: (confirmation: string, reason: string) => void;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [reason, setReason] = useState('');
  const ready = confirmation.trim() === grant.asset_name && reason.trim().length >= 3;
  return <Modal title="撤销只读诊断授权" open destroyOnHidden okText="确认撤销" onCancel={onCancel}
    confirmLoading={busy} closable={!busy} maskClosable={!busy} cancelButtonProps={{ disabled: busy }}
    okButtonProps={{ disabled: !ready || busy }} onOk={() => { if (ready && !busy) onRevoke(confirmation.trim(), reason.trim()); }}>
    <p>需输入：{grant.asset_name}</p>
    <Input aria-label="资产名称确认" value={confirmation} disabled={busy} maxLength={100} onChange={(event) => setConfirmation(event.target.value)} />
    <p>撤销原因</p><Input.TextArea aria-label="撤销原因" value={reason} disabled={busy} rows={3} maxLength={500} onChange={(event) => setReason(event.target.value)} />
  </Modal>;
}

function TestDialog({ grant, onCancel, onTest }: {
  grant: DiagnosticGrant; onCancel: () => void; onTest: (confirmation: string, reason: string) => Promise<void>;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const ready = confirmation.trim() === grant.asset_name && reason.trim().length >= 3;
  return <Modal title="验证只读诊断授权" open destroyOnHidden okText="开始验证" confirmLoading={busy}
    onCancel={() => { if (!busy) onCancel(); }} closable={!busy} maskClosable={!busy}
    cancelButtonProps={{ disabled: busy }} okButtonProps={{ disabled: !ready || busy }}
    onOk={async () => { if (!ready || busy) return; setBusy(true); await onTest(confirmation.trim(), reason.trim()); }}>
    <p>需输入：{grant.asset_name}</p>
    <Input aria-label="资产名称确认" value={confirmation} disabled={busy} maxLength={100}
      onChange={(event) => setConfirmation(event.target.value)} />
    <p>验证原因</p>
    <Input.TextArea aria-label="验证原因" value={reason} disabled={busy} rows={3} maxLength={500}
      onChange={(event) => setReason(event.target.value)} />
  </Modal>;
}
