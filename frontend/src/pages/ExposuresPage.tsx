import { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Form, Input, Modal, Progress, Select, Space, Table, Tag, message } from 'antd';
import { Eye, Network, Play, Plus, RefreshCw, Repeat2, X } from 'lucide-react';

import { getAssets } from '../api/assets';
import { cancelExposureTask, createExposureTask, getExposureTasks, rerunExposureTask, startExposureTask } from '../api/exposures';
import type { Asset } from '../types/asset';
import type { ExposureListener, ExposureStatus, ExposureTask } from '../types/exposure';

const statusText: Record<ExposureStatus, string> = { queued: '排队', scanning: '检测中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' };
const statusColor: Record<ExposureStatus, string> = { queued: 'blue', scanning: 'cyan', aggregating: 'gold', completed: 'green', failed: 'red', cancelled: 'default' };
const terminal: ExposureStatus[] = ['completed', 'failed', 'cancelled'];
const scopeText = { all_interfaces: '所有接口', specific_interface: '指定接口', loopback: '仅本机' };

export function ExposuresPage({ embedded = false }: { embedded?: boolean }) {
  const [tasks, setTasks] = useState<ExposureTask[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<ExposureTask | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ name: string; asset_ids: string[] }>();
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    try { const [items, targets] = await Promise.all([getExposureTasks(), getAssets('enabled')]); setTasks(items); setAssets(targets); }
    catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const summary = useMemo(() => ({
    total: tasks.length, running: tasks.filter((item) => ['scanning', 'aggregating'].includes(item.status)).length,
    listeners: tasks.find((item) => item.status === 'completed')?.summary?.listeners ?? 0,
    potential: tasks.find((item) => item.status === 'completed')?.summary?.potential_exposures ?? 0,
  }), [tasks]);
  const create = async () => {
    const values = await form.validateFields();
    try { await createExposureTask(values.name, values.asset_ids); setOpen(false); form.resetFields(); await load(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '创建失败'); }
  };
  const act = async (task: ExposureTask, action: 'start' | 'cancel' | 'rerun') => {
    try {
      const result = action === 'start' ? await startExposureTask(task.id) : action === 'cancel' ? await cancelExposureTask(task.id) : await rerunExposureTask(task.id);
      setTasks((current) => action === 'rerun' ? [result, ...current] : current.map((item) => item.id === result.id ? result : item));
    } catch (error) { messages.error(error instanceof Error ? error.message : '操作失败'); }
  };
  return <section className="scan-tasks-page" aria-label="暴露面检测">{holder}
    <div className="page-heading">{!embedded ? <div><p className="eyebrow">SSH 无 Agent 只读采集</p><h1>服务器暴露面</h1></div> : null}<Space><Button aria-label="刷新暴露面任务" icon={<RefreshCw size={17} />} onClick={() => void load()} /><Button type="primary" icon={<Plus size={17} />} onClick={() => setOpen(true)}>新建检测</Button></Space></div>
    <div className="metric-band"><div><span>任务总数</span><strong>{summary.total}</strong></div><div><span>检测中</span><strong>{summary.running}</strong></div><div><span>最近监听项</span><strong>{summary.listeners}</strong></div><div><span>潜在暴露</span><strong>{summary.potential}</strong></div></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={tasks} scroll={{ x: 1050 }} columns={[
      { title: '检测任务', render: (_, item: ExposureTask) => <div className="asset-identity"><span><Network size={17} /></span><div><strong>{item.name}</strong><small>{item.asset_snapshot.map((asset) => `${asset.name} · ${asset.host}`).join('、')}</small></div></div> },
      { title: '方法版本', dataIndex: 'methodology_version', width: 110 },
      { title: '状态', width: 150, render: (_, item: ExposureTask) => <div className="scan-progress"><Tag color={statusColor[item.status]}>{statusText[item.status]}</Tag><Progress percent={item.progress} size="small" showInfo={false} /></div> },
      { title: '监听 / 潜在', width: 130, render: (_, item: ExposureTask) => item.summary ? `${item.summary.listeners} / ${item.summary.potential_exposures}` : '待检测' },
      { title: '执行边界', width: 90, render: () => <Tag color="green">只读</Tag> },
      { title: '操作', width: 190, align: 'right', render: (_, item: ExposureTask) => <Space>
        <Button type="text" aria-label={`查看 ${item.name}`} icon={<Eye size={17} />} onClick={() => setSelected(item)} />
        {item.status === 'queued' ? <Button type="text" aria-label={`启动 ${item.name}`} icon={<Play size={17} />} onClick={() => void act(item, 'start')} /> : null}
        {!terminal.includes(item.status) ? <Button type="text" danger aria-label={`取消 ${item.name}`} icon={<X size={17} />} onClick={() => void act(item, 'cancel')} /> : null}
        {terminal.includes(item.status) ? <Button type="text" aria-label={`重跑 ${item.name}`} icon={<Repeat2 size={17} />} onClick={() => void act(item, 'rerun')} /> : null}
      </Space> },
    ]} /></div>
    <Modal title="新建暴露面检测" open={open} onCancel={() => setOpen(false)} onOk={() => void create()} okText="创建任务"><Form form={form} layout="vertical"><Form.Item name="name" label="任务名称" rules={[{ required: true }, { min: 2 }]}><Input /></Form.Item><Form.Item name="asset_ids" label="目标服务器" rules={[{ required: true }]}><Select mode="multiple" options={assets.map((asset) => ({ value: asset.id, label: `${asset.name} · ${asset.host}` }))} /></Form.Item><Tag color="green">只采集本机监听快照，不执行外部端口扫描</Tag></Form></Modal>
    <Drawer title="监听面证据" width={820} open={selected !== null} onClose={() => setSelected(null)}>{selected?.results.map((asset) => <section key={asset.asset_id}><h3>{asset.asset_name}</h3>{asset.message ? <Tag color="red">{asset.message}</Tag> : <Table<ExposureListener> rowKey={(item) => `${item.protocol}-${item.listen_host}-${item.port}`} pagination={false} dataSource={asset.listeners} scroll={{ x: 760 }} columns={[{ title: '协议', dataIndex: 'protocol', width: 80 }, { title: '监听地址', dataIndex: 'listen_host' }, { title: '端口', dataIndex: 'port', width: 80 }, { title: '进程', dataIndex: 'process' }, { title: '范围', dataIndex: 'scope', width: 100, render: (value: keyof typeof scopeText) => <Tag color={value === 'all_interfaces' ? 'gold' : value === 'loopback' ? 'green' : 'blue'}>{scopeText[value]}</Tag> }, { title: '结论', dataIndex: 'conclusion' }]} />}</section>)}</Drawer>
  </section>;
}
