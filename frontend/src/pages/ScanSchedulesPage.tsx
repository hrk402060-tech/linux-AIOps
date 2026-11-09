import { useEffect, useState } from 'react';
import { Button, Drawer, Form, Input, InputNumber, Modal, Radio, Select, Space, Switch, Table, Tag, TimePicker, message } from 'antd';
import { History, Plus, RefreshCw } from 'lucide-react';
import { getAssets } from '../api/assets';
import { createScanSchedule, getScanSchedules, getScheduledTasks, setScanScheduleEnabled } from '../api/scan-tasks';
import type { Asset } from '../types/asset';
import type { ScanSchedule, ScanScheduleCreate, ScanTask } from '../types/scan-task';

const types = { host_vulnerability: '主机漏洞', service_vulnerability: '服务漏洞', comprehensive: '综合扫描' };
const format = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '尚未执行';

export function ScanSchedulesPage({ embedded = false }: { embedded?: boolean }) {
  const [items, setItems] = useState<ScanSchedule[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<ScanTask[] | null>(null);
  const [kind, setKind] = useState<'daily' | 'interval'>('daily');
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    try {
      const [schedules, targets] = await Promise.all([getScanSchedules(), getAssets('enabled')]);
      setItems(schedules); setAssets(targets);
    } catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const create = async () => {
    const values = await form.validateFields();
    const payload: ScanScheduleCreate = {
      name: values.name, scan_type: values.scan_type, asset_ids: values.asset_ids,
      schedule_kind: kind, timezone: 'Asia/Shanghai',
      daily_time: kind === 'daily' ? values.daily_time.format('HH:mm') : undefined,
      interval_hours: kind === 'interval' ? values.interval_hours : undefined,
    };
    setLoading(true);
    try { await createScanSchedule(payload); setOpen(false); form.resetFields(); await load(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '创建失败'); }
    finally { setLoading(false); }
  };
  const toggle = async (item: ScanSchedule, enabled: boolean) => {
    try {
      const saved = await setScanScheduleEnabled(item.id, enabled);
      setItems((current) => current.map((value) => value.id === saved.id ? saved : value));
    } catch (error) { messages.error(error instanceof Error ? error.message : '状态更新失败'); }
  };
  const showHistory = async (item: ScanSchedule) => {
    try { setHistory(await getScheduledTasks(item.id)); }
    catch (error) { messages.error(error instanceof Error ? error.message : '历史加载失败'); }
  };
  return <section className="scan-tasks-page" aria-label="定时扫描">{holder}
    <div className="page-heading">{!embedded ? <div><p className="eyebrow">只读周期检测</p><h1>定时扫描</h1></div> : null}<Space><Button aria-label="刷新计划" icon={<RefreshCw size={17} />} onClick={() => void load()} /><Button type="primary" icon={<Plus size={17} />} onClick={() => setOpen(true)}>新建计划</Button></Space></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={items} pagination={{ pageSize: 10, showSizeChanger: false }} columns={[
      { title: '计划', dataIndex: 'name' }, { title: '扫描类型', dataIndex: 'scan_type', render: (value: keyof typeof types) => types[value] },
      { title: '资产', dataIndex: 'asset_snapshot', render: (value: Array<{ name: string }>) => value.map((item) => item.name).join('、') },
      { title: '规则', render: (_, item: ScanSchedule) => item.schedule_kind === 'daily' ? `每天 ${item.daily_time}` : `每 ${item.interval_hours} 小时` },
      { title: '上次执行', dataIndex: 'last_run_at', render: format }, { title: '下次执行', dataIndex: 'next_run_at', render: format },
      { title: '启用', render: (_, item: ScanSchedule) => <Switch checked={item.enabled} aria-label={`${item.enabled ? '停用' : '启用'} ${item.name}`} onChange={(value) => void toggle(item, value)} /> },
      { title: '操作', render: (_, item: ScanSchedule) => <Button icon={<History size={16} />} aria-label={`查看 ${item.name} 执行历史`} onClick={() => void showHistory(item)}>执行历史</Button> },
    ]} /></div>
    <Modal title="新建扫描计划" open={open} onCancel={() => setOpen(false)} onOk={() => void create()} confirmLoading={loading} okText="创建计划"><Form form={form} layout="vertical">
      <Form.Item name="name" label="计划名称" rules={[{ required: true }, { min: 2 }]}><Input /></Form.Item>
      <Form.Item name="scan_type" label="扫描类型" rules={[{ required: true }]}><Select options={Object.entries(types).map(([value, label]) => ({ value, label }))} /></Form.Item>
      <Form.Item name="asset_ids" label="目标服务器" rules={[{ required: true }]}><Select mode="multiple" options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} /></Form.Item>
      <Form.Item label="执行方式"><Radio.Group value={kind} onChange={(event) => setKind(event.target.value)} options={[{ value: 'daily', label: '每日定时' }, { value: 'interval', label: '固定周期' }]} /></Form.Item>
      {kind === 'daily' ? <Form.Item name="daily_time" label="执行时间（Asia/Shanghai）" rules={[{ required: true }]}><TimePicker format="HH:mm" /></Form.Item> : <Form.Item name="interval_hours" label="间隔小时" rules={[{ required: true }]}><InputNumber min={1} max={720} /></Form.Item>}
      <Tag color="green">所有计划仅创建只读扫描任务</Tag>
    </Form></Modal>
    <Drawer title="计划执行历史" open={history !== null} onClose={() => setHistory(null)} width={720}><Table rowKey="id" dataSource={history ?? []} pagination={false} columns={[{ title: '任务', dataIndex: 'name' }, { title: '状态', dataIndex: 'status' }, { title: '知识版本', dataIndex: 'knowledge_version' }, { title: '创建时间', dataIndex: 'created_at', render: format }]} /></Drawer>
  </section>;
}
