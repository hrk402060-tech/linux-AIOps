import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Select, Space, Spin, Table, Tag, Tooltip, message } from 'antd';
import { History, Play, Plus, RefreshCw, Settings2 } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import { getCmdbTree } from '../api/cmdb';
import { createProbe, getProbePolicy, getProbeResults, getProbes, runProbe, updateProbe, updateProbePolicy, type HttpProbe, type ProbeCreate, type ProbeResult } from '../api/probes';
import type { CmdbServiceTree } from '../types/cmdb';

const errorLabels: Record<string, string> = { timeout: '超时', tls_error: 'TLS 校验失败', connection_error: '连接失败', http_status_mismatch: 'HTTP 状态不符合预期' };
function resultTag(result: ProbeResult | null) {
  return result ? <Tag color={result.status === 'healthy' ? 'green' : 'red'}>{result.status === 'healthy' ? '通过' : errorLabels[result.error_code ?? ''] ?? '失败'}</Tag> : <Tag>未检查</Tag>;
}
function time(value: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'; }

export function ProbesPage({ currentUser, initialServiceId, initialProbe }: {
  currentUser: AuthUser; initialServiceId?: string; initialProbe?: { id: string; name: string };
}) {
  const readOnly = currentUser.role === 'auditor';
  const isAdmin = currentUser.role === 'admin';
  const [items, setItems] = useState<HttpProbe[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<string | undefined>(initialServiceId);
  const [services, setServices] = useState<CmdbServiceTree[]>([]);
  const [cidrs, setCidrs] = useState<string[]>([]);
  const [policySource, setPolicySource] = useState('');
  const [policyOpen, setPolicyOpen] = useState(false);
  const [policyText, setPolicyText] = useState('');
  const [policySaving, setPolicySaving] = useState(false);
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policyReady, setPolicyReady] = useState(false);
  const [policyError, setPolicyError] = useState('');
  const policyRequest = useRef(0);
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [historyTarget, setHistoryTarget] = useState<HttpProbe>();
  const [history, setHistory] = useState<ProbeResult[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [initialHistoryOpened, setInitialHistoryOpened] = useState(false);
  const [form] = Form.useForm<ProbeCreate>();
  const [messageApi, contextHolder] = message.useMessage();
  const serviceId = Form.useWatch('service_id', form);
  const service = services.find((item) => item.id === serviceId);
  const fail = (value: unknown) => setError(value instanceof Error ? value.message : '拨测请求失败');
  const load = async () => {
    setLoading(true);
    try { const data = await getProbes(filter, page); setItems(data.items); setTotal(data.total); setHasLoaded(true); }
    catch (value) { fail(value); } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [filter, page]);
  const loadPolicy = async () => {
    const request = ++policyRequest.current;
    setPolicyLoading(true); setPolicyReady(false); setPolicyError(''); setPolicyOpen(false); setPolicySaving(false);
    try {
      const policy = await getProbePolicy();
      if (request !== policyRequest.current) return;
      setCidrs(policy.allowed_cidrs); setPolicySource(policy.source ?? '');
      setPolicyText(policy.allowed_cidrs.join('\n')); setPolicyReady(true);
    } catch (value) {
      if (request === policyRequest.current) setPolicyError(value instanceof Error ? value.message : '批准网段读取失败');
    } finally { if (request === policyRequest.current) setPolicyLoading(false); }
  };
  useEffect(() => {
    let active = true;
    void loadPolicy();
    void getCmdbTree().then((tree) => {
      if (active) setServices(tree.businesses.flatMap((business) => business.applications.filter((app) => app.lifecycle_status === 'active').flatMap((app) => app.services.filter((item) => item.lifecycle_status === 'active'))));
    }).catch((value: unknown) => { if (active) fail(value); });
    return () => { active = false; ++policyRequest.current; };
  }, [currentUser.id, currentUser.role]);
  const canProbe = policyReady && !policyLoading && cidrs.length > 0;
  const savePolicy = async () => {
    if (!isAdmin || !policyReady || policyLoading || policySaving) return;
    const request = policyRequest.current;
    setPolicySaving(true);
    try {
      const policy = await updateProbePolicy(policyText);
      if (request !== policyRequest.current) return;
      setCidrs(policy.allowed_cidrs);
      setPolicySource(policy.source ?? '平台配置');
      setPolicyText(policy.allowed_cidrs.join('\n'));
      setPolicyOpen(false);
      messageApi.success(policy.allowed_cidrs.length ? '批准网段已保存' : '批准网段已清空，拨测将暂停');
    } catch (value) { if (request === policyRequest.current) fail(value); }
    finally { if (request === policyRequest.current) setPolicySaving(false); }
  };
  const run = async (target: HttpProbe) => {
    if (readOnly || !canProbe) return;
    setBusy(target.id); setError('');
    try { await runProbe(target.id); await load(); } catch (value) { fail(value); } finally { setBusy(undefined); }
  };
  const toggle = async (target: HttpProbe) => {
    setBusy(target.id); setError('');
    try { await updateProbe(target.id, !target.enabled); await load(); } catch (value) { fail(value); } finally { setBusy(undefined); }
  };
  const readHistory = async (target: HttpProbe, next = 1) => {
    setHistoryTarget(target); setHistoryPage(next); setHistory([]); setHistoryLoading(true);
    try { const data = await getProbeResults(target.id, next); setHistory(data.items); setHistoryTotal(data.total); }
    catch (value) { fail(value); } finally { setHistoryLoading(false); }
  };
  useEffect(() => {
    if (!initialProbe || initialHistoryOpened) return;
    setInitialHistoryOpened(true);
    const target: HttpProbe = { ...initialProbe, service_id: initialServiceId ?? '', service_name: '', asset_id: '', asset_name: '', api_id: null, url: '', method: 'HEAD', expected_status: 200, timeout_seconds: 5, enabled: true, created_at: '', latest_result: null };
    void readHistory(target);
  }, [initialProbe, initialHistoryOpened, initialServiceId]);
  const save = async (payload: ProbeCreate) => {
    if (readOnly || !canProbe) return;
    setSaving(true); setError('');
    try { await createProbe({ ...payload, api_id: payload.api_id ?? null }); setOpen(false); form.resetFields(); await load(); }
    catch (value) { fail(value); } finally { setSaving(false); }
  };
  return <section>{contextHolder}{error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    <div className="page-heading"><div><p className="eyebrow">运维中心</p><h1>服务/API 拨测</h1></div><Space>{isAdmin && policyReady ? <Button size="small" icon={<Settings2 size={15} />} onClick={() => setPolicyOpen(true)}>批准网段</Button> : null}<Tooltip title="刷新"><Button aria-label="刷新拨测" icon={<RefreshCw size={16} />} onClick={() => void load()} /></Tooltip><Button icon={<Plus size={16} />} disabled={readOnly || !canProbe} onClick={() => { form.resetFields(); setOpen(true); }}>新增目标</Button></Space></div>
    {policyError ? <Alert type="error" showIcon message="批准网段读取失败" description={<>{policyError}<Button type="link" aria-label="重试读取批准网段" onClick={() => void loadPolicy()}>重试</Button></>} /> : null}
    <Space style={{ margin: '16px 0' }}><Select aria-label="筛选服务" style={{ width: 240 }} allowClear placeholder="全部服务" value={filter} onChange={(value) => { setFilter(value); setPage(1); }} options={services.map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} /></Space>
    {!hasLoaded && loading ? <Spin aria-label="正在读取拨测目标" /> : null}
    {hasLoaded ? <Table rowKey="id" loading={loading} dataSource={items} scroll={{ x: 1050 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无拨测目标" /> }} pagination={{ current: page, pageSize: 10, total, showSizeChanger: false, onChange: setPage }} columns={[
      { title: '目标 / 服务', width: 200, render: (_, item: HttpProbe) => <div><strong>{item.name}</strong><div>{item.service_name} · {item.asset_name}</div></div> },
      { title: '地址', dataIndex: 'url', width: 300, render: (value: string) => <span style={{ overflowWrap: 'anywhere' }}>{value}</span> },
      { title: '规则', width: 110, render: (_, item: HttpProbe) => `${item.method} → ${item.expected_status}` },
      { title: '最新结果', width: 160, render: (_, item: HttpProbe) => <div>{resultTag(item.latest_result)}<div>{item.latest_result ? `${item.latest_result.http_status ?? '-'} · ${item.latest_result.latency_ms}ms` : '-'}</div></div> },
      { title: '检查时间', width: 180, render: (_, item: HttpProbe) => item.latest_result ? time(item.latest_result.checked_at) : '未检查' },
      { title: '操作', width: 170, render: (_, item: HttpProbe) => <Space><Tooltip title="人工检查"><Button aria-label={`检查 ${item.name}`} icon={<Play size={15} />} loading={busy === item.id} disabled={readOnly || !canProbe || !item.enabled || Boolean(busy)} onClick={() => void run(item)} /></Tooltip><Tooltip title="检查历史"><Button aria-label={`历史 ${item.name}`} icon={<History size={15} />} onClick={() => void readHistory(item)} /></Tooltip><Button size="small" disabled={readOnly || Boolean(busy)} onClick={() => void toggle(item)}>{item.enabled ? '停用' : '启用'}</Button></Space> },
    ]} /> : null}
    <Modal title="新增拨测目标" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ method: 'HEAD', expected_status: 200, timeout_seconds: 5 }} onFinish={(payload) => void save(payload)}>
        <Form.Item name="name" label="目标名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="service_id" label="目录服务" rules={[{ required: true }]}><Select options={services.map((item) => ({ value: item.id, label: `${item.name} · ${item.code}` }))} onChange={() => form.setFieldsValue({ asset_id: undefined, api_id: null })} /></Form.Item>
        <Form.Item name="asset_id" label="承载服务器" rules={[{ required: true }]}><Select options={(service?.assets ?? []).filter((item) => item.status === 'active').map((item) => ({ value: item.asset_id, label: `${item.asset_name} · ${item.asset_host}` }))} /></Form.Item>
        <Form.Item name="api_id" label="关联 API"><Select allowClear options={(service?.apis ?? []).filter((item) => item.lifecycle_status === 'active' && ['GET', 'HEAD'].includes(item.http_method)).map((item) => ({ value: item.id, label: `${item.name} · ${item.http_method} ${item.path}` }))} onChange={(value) => { const api = service?.apis.find((item) => item.id === value); if (api) form.setFieldValue('method', api.http_method); }} /></Form.Item>
        <Form.Item name="url" label="完整 URL（IP 必须与服务器一致，路径与关联 API 一致）" rules={[{ required: true }]}><Input placeholder="http://192.0.2.40:8080/health" maxLength={1000} /></Form.Item>
        <Form.Item name="method" label="方法"><Select options={[{ value: 'HEAD' }, { value: 'GET' }]} /></Form.Item>
        <Space><Form.Item name="expected_status" label="预期 HTTP 状态" rules={[{ required: true }]}><InputNumber min={200} max={599} /></Form.Item><Form.Item name="timeout_seconds" label="超时（秒）" rules={[{ required: true }]}><InputNumber min={1} max={5} /></Form.Item></Space>
        <Button htmlType="submit" type="primary" loading={saving} disabled={readOnly || !canProbe}>保存目标</Button>
      </Form>
    </Modal>
    <Modal title="拨测批准网段" open={isAdmin && policyOpen && policyReady} onCancel={() => setPolicyOpen(false)} onOk={() => void savePolicy()} okText="保存配置" cancelText="取消" confirmLoading={policySaving} okButtonProps={{ disabled: !isAdmin || !policyReady || policyLoading }} destroyOnHidden>
      <Input.TextArea aria-label="批准网段" value={policyText} onChange={(event) => setPolicyText(event.target.value.replace(/,/g, '\n'))} rows={5} placeholder="192.0.2.0/24&#10;198.51.100.10/32" />
    </Modal>
    <Drawer title="检查历史" open={Boolean(historyTarget)} width={800} onClose={() => setHistoryTarget(undefined)}>
      <p>{historyTarget?.name}</p><Table rowKey="id" loading={historyLoading} dataSource={history} scroll={{ x: 850 }} pagination={{ current: historyPage, pageSize: 10, total: historyTotal, showSizeChanger: false, onChange: (next) => { if (historyTarget) void readHistory(historyTarget, next); } }} columns={[
        { title: '时间', dataIndex: 'checked_at', render: time, width: 180 }, { title: '结论', render: (_, item: ProbeResult) => resultTag(item), width: 120 },
        { title: '实际 / 预期', render: (_, item: ProbeResult) => `${item.http_status ?? '-'} / ${item.expected_status}`, width: 120 },
        { title: '耗时', dataIndex: 'latency_ms', render: (value: number) => `${value}ms`, width: 90 }, { title: '操作人', dataIndex: 'operator_username', width: 120 },
        { title: '证据', render: (_, item: ProbeResult) => <div style={{ overflowWrap: 'anywhere' }}>{item.method} {item.url}<div>trace: {item.trace_id}</div><div>service: {item.service_id} · asset: {item.asset_id}</div></div>, width: 260 },
      ]} />
    </Drawer>
  </section>;
}
