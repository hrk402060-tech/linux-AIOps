import { useEffect, useState } from 'react';
import {
  Button, DatePicker, Drawer, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, message,
} from 'antd';
import { History, Pencil, RefreshCw, Settings } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import { getAssets } from '../../api/assets';
import {
  eventRequest, getMonitoringOverview, updateMonitoringPolicy,
  type EventAudit, type MonitoringPolicy, type SecurityEvent,
} from '../../api/events';
import type { Asset } from '../../types/asset';

const statuses: Record<string, string> = {
  open: '待处理', acknowledged: '已确认', in_progress: '处理中', closed: '人工关闭',
};
const sources: Record<string, string> = {
  monitoring: '服务器监控', scan: '漏洞扫描', vulnerability: '漏洞风险', knowledge: '知识库同步',
};
const levels: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危' };
const options = (values: Record<string, string>) =>
  Object.entries(values).map(([value, label]) => ({ value, label }));

export function ActiveEventsTab({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [data, setData] = useState<{ items: SecurityEvent[]; total: number }>({ items: [], total: 0 });
  const [assets, setAssets] = useState<Asset[]>([]);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [target, setTarget] = useState<SecurityEvent | null>(null);
  const [status, setStatus] = useState('acknowledged');
  const [reason, setReason] = useState('');
  const [audit, setAudit] = useState<EventAudit[] | null>(null);
  const [policies, setPolicies] = useState<Array<{ asset_id: string; asset_name: string; policy: MonitoringPolicy }>>([]);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [policyAsset, setPolicyAsset] = useState('');
  const [policy, setPolicy] = useState<MonitoringPolicy | null>(null);
  const [messages, holder] = message.useMessage();
  const readOnly = currentUser.role === 'auditor';
  const failure = (error: unknown) => messages.error(error instanceof Error ? error.message : '请求失败');
  const load = async () => {
    setLoading(true);
    try { setData(await eventRequest(`/api/v1/events?${new URLSearchParams({ ...filters, page: String(page) })}`)); }
    catch (error) { failure(error); } finally { setLoading(false); }
  };
  useEffect(() => {
    void Promise.all([getAssets('active'), getMonitoringOverview()])
      .then(([nextAssets, overview]) => { setAssets(nextAssets); setPolicies(overview.assets); })
      .catch(failure);
  }, []);
  useEffect(() => { void load(); }, [filters, page]);
  const filter = (key: string, value?: string) => {
    setPage(1);
    setFilters((current) => {
      const next = { ...current };
      if (value) next[key] = value; else delete next[key];
      return next;
    });
  };
  const save = async () => {
    if (!target || reason.trim().length < 3) return;
    setLoading(true);
    try {
      await eventRequest(`/api/v1/events/${encodeURIComponent(target.id)}/disposition`, { status, reason });
      setTarget(null); await load();
    } catch (error) { failure(error); } finally { setLoading(false); }
  };
  const history = async (item: SecurityEvent) => {
    try { setAudit(await eventRequest(`/api/v1/events/${encodeURIComponent(item.id)}/audit`)); }
    catch (error) { failure(error); }
  };
  const choosePolicy = (assetId: string) => {
    setPolicyAsset(assetId);
    setPolicy(policies.find((item) => item.asset_id === assetId)?.policy ?? null);
  };
  const savePolicy = async () => {
    if (!policy) return;
    setLoading(true);
    try {
      const saved = await updateMonitoringPolicy(policyAsset, policy);
      setPolicies((current) => current.map((item) => item.asset_id === policyAsset ? { ...item, policy: saved } : item));
      setPolicyOpen(false); messages.success('告警策略已更新');
    } catch (error) { failure(error); } finally { setLoading(false); }
  };
  const number = (key: keyof MonitoringPolicy, value: number | null) =>
    setPolicy((current) => current ? { ...current, [key]: value ?? 0 } : current);
  return <div>{holder}
    <div className="tab-toolbar"><Space wrap>{readOnly ? <Tag>审计员只读</Tag> : <Button icon={<Settings size={17} />} onClick={() => setPolicyOpen(true)}>告警策略</Button>}<Button aria-label="刷新事件" icon={<RefreshCw size={17} />} onClick={() => void load()} /></Space></div>
    <Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear aria-label="事件资产" placeholder="全部资产" style={{ width: 220 }} options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} onChange={(value) => filter('asset_id', value)} />
      <Select allowClear aria-label="事件来源" placeholder="全部来源" style={{ width: 150 }} options={options(sources)} onChange={(value) => filter('source', value)} />
      <Select allowClear aria-label="事件级别" placeholder="全部级别" style={{ width: 120 }} options={options(levels)} onChange={(value) => filter('severity', value)} />
      <Select allowClear aria-label="处置状态" placeholder="全部处置状态" style={{ width: 150 }} options={options(statuses)} onChange={(value) => filter('status', value)} />
      <DatePicker.RangePicker onChange={(dates) => { filter('since', dates?.[0]?.startOf('day').toISOString()); filter('until', dates?.[1]?.endOf('day').toISOString()); }} />
    </Space>
    <Table<SecurityEvent> rowKey="id" loading={loading} dataSource={data.items} scroll={{ x: 1000 }} pagination={{ current: page, pageSize: 20, total: data.total, onChange: setPage, showSizeChanger: false }} columns={[
      { title: '事件', dataIndex: 'title' }, { title: '资产', dataIndex: 'asset_name' },
      { title: '来源', dataIndex: 'source', render: (value: string) => sources[value] ?? value },
      { title: '级别', dataIndex: 'severity', render: (value: string) => <Tag color={['critical', 'high'].includes(value) ? 'red' : 'gold'}>{levels[value] ?? value}</Tag> },
      { title: '处置状态', dataIndex: 'status', render: (value: string) => statuses[value] ?? value },
      { title: '恢复状态', dataIndex: 'recovered', render: (value: boolean) => value ? '源已恢复' : '未确认恢复' },
      { title: '时间', dataIndex: 'occurred_at', render: (value: string) => new Date(value).toLocaleString() },
      { title: '操作', render: (_, item) => <Space>{!readOnly ? <Button aria-label={`处置 ${item.title}`} icon={<Pencil size={16} />} onClick={() => { setTarget(item); setReason(''); setStatus('acknowledged'); }} /> : null}<Button aria-label={`审计 ${item.title}`} icon={<History size={16} />} onClick={() => void history(item)} /></Space> },
    ]} />
    <Modal title="事件处置" open={target !== null} onCancel={() => setTarget(null)} onOk={() => void save()} confirmLoading={loading} okButtonProps={{ disabled: reason.trim().length < 3 }}><p>{target?.title}</p><Select aria-label="新处置状态" value={status} options={options(statuses)} onChange={setStatus} style={{ width: '100%', marginBottom: 12 }} /><Input.TextArea aria-label="处置原因" placeholder="处置原因（至少 3 个字符）" value={reason} onChange={(event) => setReason(event.target.value)} /></Modal>
    <Drawer title="事件处置记录" open={audit !== null} onClose={() => setAudit(null)} width={560}><Table rowKey={(_, index) => String(index)} pagination={false} dataSource={audit ?? []} columns={[{ title: '状态', dataIndex: 'status', render: (value: string) => statuses[value] ?? value }, { title: '原因', dataIndex: 'reason' }, { title: '操作人', dataIndex: 'operator' }, { title: '时间', dataIndex: 'created_at' }]} /></Drawer>
    <Modal title="服务器告警策略" open={policyOpen} onCancel={() => setPolicyOpen(false)} onOk={() => void savePolicy()} confirmLoading={loading} okButtonProps={{ disabled: !policy }}><div className="event-policy-form"><Select aria-label="策略服务器" placeholder="选择服务器" value={policyAsset || undefined} onChange={choosePolicy} options={policies.map((item) => ({ value: item.asset_id, label: item.asset_name }))} />{policy ? <><div className="event-policy-grid"><label>CPU %<InputNumber value={policy.cpu_threshold} min={1} max={100} onChange={(value) => number('cpu_threshold', value)} /></label><label>内存 %<InputNumber value={policy.memory_threshold} min={1} max={100} onChange={(value) => number('memory_threshold', value)} /></label><label>Swap %<InputNumber value={policy.swap_threshold} min={1} max={100} onChange={(value) => number('swap_threshold', value)} /></label><label>磁盘 %<InputNumber value={policy.disk_threshold} min={1} max={100} onChange={(value) => number('disk_threshold', value)} /></label><label>inode %<InputNumber value={policy.inode_threshold} min={1} max={100} onChange={(value) => number('inode_threshold', value)} /></label><label>负载<InputNumber value={policy.load_threshold} min={0.1} max={1000} onChange={(value) => number('load_threshold', value)} /></label><label>持续分钟<InputNumber value={policy.duration_minutes} min={0} max={1440} onChange={(value) => number('duration_minutes', value)} /></label><label>恢复通知<Switch checked={policy.recovery_notify} onChange={(value) => setPolicy({ ...policy, recovery_notify: value })} /></label></div><DatePicker.RangePicker showTime aria-label="维护窗口" onChange={(dates) => setPolicy({ ...policy, maintenance_start: dates?.[0]?.toISOString() ?? null, maintenance_end: dates?.[1]?.toISOString() ?? null })} /></> : null}</div></Modal>
  </div>;
}
