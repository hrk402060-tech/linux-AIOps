import { useEffect, useMemo, useState } from 'react';
import {
  Button, Drawer, Empty, Form, Input, Modal, Select, Space, Table, Tag, Timeline, message,
} from 'antd';
import { Eye, History, Plus, RefreshCw, StepForward } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import { getAssets } from '../api/assets';
import {
  advanceIncident, createIncident, getIncidents, getIncidentTimeline,
  type Incident, type IncidentCreate, type IncidentSeverity, type IncidentStatus,
  type IncidentTimelineEntry,
} from '../api/incidents';
import { IncidentDetailDrawer } from '../components/IncidentDetailDrawer';
import type { Asset } from '../types/asset';

const statusLabels: Record<IncidentStatus, string> = {
  open: '待确认', acknowledged: '已确认', in_progress: '处理中', resolved: '人工登记完成', closed: '人工关闭',
};
const severityLabels: Record<IncidentSeverity, string> = {
  critical: '严重', high: '高危', medium: '中危', low: '低危',
};
const nextStatus: Partial<Record<IncidentStatus, IncidentStatus>> = {
  open: 'acknowledged', acknowledged: 'in_progress', in_progress: 'resolved', resolved: 'closed',
};
const severityColors: Record<IncidentSeverity, string> = {
  critical: 'red', high: 'volcano', medium: 'gold', low: 'blue',
};
const statusColors: Record<IncidentStatus, string> = {
  open: 'red', acknowledged: 'gold', in_progress: 'blue', resolved: 'default', closed: 'default',
};
const slaLabels = { on_track: 'SLA 正常', at_risk: 'SLA 临近', breached: 'SLA 已超时', met: 'SLA 已达标' };
const slaColors = { on_track: 'green', at_risk: 'gold', breached: 'red', met: 'blue' };
const selectOptions = (values: Record<string, string>) =>
  Object.entries(values).map(([value, label]) => ({ value, label }));

export function IncidentsPage({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [data, setData] = useState<{ total: number; items: Incident[] }>({ total: 0, items: [] });
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [target, setTarget] = useState<Incident | null>(null);
  const [note, setNote] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [timeline, setTimeline] = useState<IncidentTimelineEntry[] | null>(null);
  const [timelineTitle, setTimelineTitle] = useState('');
  const [detail, setDetail] = useState<Incident | null>(null);
  const [form] = Form.useForm<IncidentCreate>();
  const [messages, holder] = message.useMessage();
  const readOnly = currentUser.role === 'auditor';
  const load = async () => {
    setLoading(true);
    try { setData(await getIncidents(filters)); }
    catch (error) { messages.error(error instanceof Error ? error.message : '故障列表加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [filters]);
  const metrics = useMemo(() => ({
    active: data.items.filter((item) => item.status !== 'closed').length,
    urgent: data.items.filter((item) => ['critical', 'high'].includes(item.severity) && item.status !== 'closed').length,
    processing: data.items.filter((item) => item.status === 'in_progress').length,
    resolved: data.items.filter((item) => item.status === 'resolved').length,
    breached: data.items.filter((item) => item.sla_status === 'breached').length,
  }), [data.items]);
  const updateFilter = (key: string, value?: string) => setFilters((current) => {
    const next = { ...current };
    if (value) next[key] = value; else delete next[key];
    return next;
  });
  const openCreate = async () => {
    setCreateOpen(true);
    if (assets.length === 0) {
      try { setAssets(await getAssets('active')); }
      catch (error) { messages.error(error instanceof Error ? error.message : '资产加载失败'); }
    }
  };
  const submitCreate = async () => {
    const values = await form.validateFields();
    setLoading(true);
    try {
      await createIncident({ ...values, alert_id: values.alert_id?.trim() || undefined });
      messages.success('故障已创建并写入时间线');
      setCreateOpen(false); form.resetFields(); await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '故障创建失败'); }
    finally { setLoading(false); }
  };
  const submitAdvance = async () => {
    if (!target || note.trim().length < 3) return;
    const status = nextStatus[target.status];
    if (!status) return;
    if (status === 'closed' && confirmation.trim() !== '确认关闭') return;
    setLoading(true);
    try {
      await advanceIncident(target.id, status, note.trim());
      messages.success(`故障已推进至${statusLabels[status]}`);
      setTarget(null); setNote(''); setConfirmation(''); await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '状态推进失败'); }
    finally { setLoading(false); }
  };
  const showTimeline = async (incident: Incident) => {
    setTimelineTitle(incident.title);
    try { setTimeline(await getIncidentTimeline(incident.id)); }
    catch (error) { messages.error(error instanceof Error ? error.message : '时间线加载失败'); }
  };
  const advancingTo = target ? nextStatus[target.status] : undefined;
  const canAdvance = note.trim().length >= 3
    && (advancingTo !== 'closed' || confirmation.trim() === '确认关闭');
  return <section className="incidents-page">{holder}
    <div className="page-heading"><div><p className="eyebrow">人工处置 · 全程留痕</p><h1>故障与工单</h1></div><Space>{readOnly ? <Tag>审计员只读</Tag> : <Button type="primary" icon={<Plus size={16} />} onClick={() => void openCreate()}>新建故障</Button>}<Button aria-label="刷新故障" icon={<RefreshCw size={17} />} onClick={() => void load()} /></Space></div>
    <div className="metric-band"><div><span>当前故障</span><strong>{metrics.active}</strong></div><div><span>严重 / 高危</span><strong>{metrics.urgent}</strong></div><div><span>处理中</span><strong>{metrics.processing}</strong></div><div><span>待人工关闭</span><strong>{metrics.resolved}</strong></div><div><span>SLA 超时</span><strong>{metrics.breached}</strong></div></div>
    <Space wrap style={{ marginBottom: 14 }}>
      <Input.Search allowClear aria-label="搜索故障" placeholder="搜索标题" style={{ width: 240 }} onSearch={(value) => updateFilter('query', value.trim())} />
      <Select allowClear aria-label="故障级别" placeholder="全部级别" style={{ width: 130 }} options={selectOptions(severityLabels)} onChange={(value) => updateFilter('severity', value)} />
      <Select allowClear aria-label="故障状态" placeholder="全部状态" style={{ width: 150 }} options={selectOptions(statusLabels)} onChange={(value) => updateFilter('status', value)} />
    </Space>
    <div className="table-frame"><Table<Incident> rowKey="id" loading={loading} dataSource={data.items} pagination={{ pageSize: 20, total: data.total, showSizeChanger: false }} scroll={{ x: 1080 }} locale={{ emptyText: <Empty description="暂无故障记录" /> }} columns={[
      { title: '故障', width: 260, render: (_, item) => <div className="asset-identity"><div><strong>{item.title}</strong><small>{item.description}</small></div></div> },
      { title: '资产', dataIndex: 'asset_name', width: 150, render: (value: string, item) => <span>{value || item.asset_id}</span> },
      { title: '级别', dataIndex: 'severity', width: 90, render: (value: IncidentSeverity) => <Tag color={severityColors[value]}>{severityLabels[value]}</Tag> },
      { title: '状态', dataIndex: 'status', width: 110, render: (value: IncidentStatus) => <Tag color={statusColors[value]}>{statusLabels[value]}</Tag> },
      { title: 'SLA', width: 130, render: (_, item) => <Tag color={slaColors[item.sla_status ?? 'on_track']}>{slaLabels[item.sla_status ?? 'on_track']}</Tag> },
      { title: '关联告警', dataIndex: 'alert_id', width: 190, render: (value: string | null) => value ?? '-' },
      { title: '创建时间', dataIndex: 'created_at', width: 170, render: (value: string) => new Date(value).toLocaleString() },
      { title: '操作', fixed: 'right', width: 150, render: (_, item) => <Space>{!readOnly && nextStatus[item.status] ? <Button aria-label={`推进 ${item.title}`} icon={<StepForward size={16} />} onClick={() => { setTarget(item); setNote(''); setConfirmation(''); }} /> : null}<Button aria-label={`查看 ${item.title} 详情`} icon={<Eye size={16} />} onClick={() => setDetail(item)} /><Button aria-label={`查看 ${item.title} 时间线`} icon={<History size={16} />} onClick={() => void showTimeline(item)} /></Space> },
    ]} /></div>
    <Modal title="新建故障" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => void submitCreate()} okText="创建故障" confirmLoading={loading} destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ severity: 'medium' }}>
        <Form.Item name="title" label="故障标题" rules={[{ required: true }, { min: 3 }]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="asset_id" label="受影响资产" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} /></Form.Item>
        <Form.Item name="severity" label="故障级别" rules={[{ required: true }]}><Select aria-label="新建故障级别" options={selectOptions(severityLabels)} /></Form.Item>
        <Form.Item name="description" label="影响说明" rules={[{ required: true }, { min: 3 }]}><Input.TextArea rows={3} maxLength={1000} /></Form.Item>
        <Form.Item name="alert_id" label="关联告警 ID（可选）"><Input maxLength={120} /></Form.Item>
      </Form>
    </Modal>
    <Modal title="人工推进故障" open={target !== null} onCancel={() => setTarget(null)} onOk={() => void submitAdvance()} okText="确认推进" confirmLoading={loading} okButtonProps={{ disabled: !canAdvance }} destroyOnHidden>{target && advancingTo ? <><p><strong>{target.title}</strong></p><p>当前状态：{statusLabels[target.status]} → <strong>{statusLabels[advancingTo]}</strong>。此操作将写入不可变时间线。</p><Input.TextArea aria-label="处置备注" value={note} onChange={(event) => setNote(event.target.value)} rows={3} maxLength={1000} placeholder="请输入人工处置备注（至少 3 个字符）" />{advancingTo === 'closed' ? <><p className="confirmation-prompt">关闭必须由人工确认，请输入：<strong>确认关闭</strong></p><Input aria-label="关闭确认文本" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="确认关闭" /></> : null}</> : null}</Modal>
    <IncidentDetailDrawer incident={detail} currentUser={currentUser} onClose={() => setDetail(null)} />
    <Drawer title={`${timelineTitle} · 不可变时间线`} open={timeline !== null} onClose={() => setTimeline(null)} width={560}><Timeline items={(timeline ?? []).map((entry) => ({ color: statusColors[entry.status], children: <div className="timeline-entry"><strong>{statusLabels[entry.status]}</strong><span>{entry.note}</span><small>{entry.operator} · {new Date(entry.created_at).toLocaleString()}</small></div> }))} /></Drawer>
  </section>;
}
