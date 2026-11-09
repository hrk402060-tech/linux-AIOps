import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Drawer, Form, Input, Modal, Progress, Select, Space, Spin, Table, Tag, message } from 'antd';
import { Eye, Play, Plus, RefreshCw, Repeat2, ShieldCheck, X } from 'lucide-react';

import { getAssets } from '../api/assets';
import { cancelBaselineTask, createBaselineTask, getBaselineTasks, rerunBaselineTask, startBaselineTask } from '../api/baselines';
import type { Asset } from '../types/asset';
import type { BaselineCheck, BaselineStatus, BaselineTask } from '../types/baseline';

const statusText: Record<BaselineStatus, string> = { queued: '排队', scanning: '检查中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' };
const statusColor: Record<BaselineStatus, string> = { queued: 'blue', scanning: 'cyan', aggregating: 'gold', completed: 'green', failed: 'red', cancelled: 'default' };
const terminal: BaselineStatus[] = ['completed', 'failed', 'cancelled'];

export function BaselinesPage({ embedded = false }: { embedded?: boolean }) {
  const [tasks, setTasks] = useState<BaselineTask[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<BaselineTask | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [form] = Form.useForm<{ name: string; asset_ids: string[] }>();
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    setLoadError('');
    try { const [items, targets] = await Promise.all([getBaselineTasks(), getAssets('enabled')]); setTasks(items); setAssets(targets); }
    catch (error) { const text = error instanceof Error ? error.message : '加载失败'; setLoadError(text); messages.error(text); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const summary = useMemo(() => ({
    total: tasks.length, running: tasks.filter((item) => ['scanning', 'aggregating'].includes(item.status)).length,
    failed: tasks.filter((item) => item.status === 'failed').length,
    rate: tasks.find((item) => item.status === 'completed')?.summary?.compliance_rate ?? 0,
  }), [tasks]);
  const create = async () => {
    const values = await form.validateFields();
    try { await createBaselineTask(values.name, values.asset_ids); setOpen(false); form.resetFields(); await load(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '创建失败'); }
  };
  const act = async (task: BaselineTask, action: 'start' | 'cancel' | 'rerun') => {
    try {
      const result = action === 'start' ? await startBaselineTask(task.id) : action === 'cancel' ? await cancelBaselineTask(task.id) : await rerunBaselineTask(task.id);
      setTasks((current) => action === 'rerun' ? [result, ...current] : current.map((item) => item.id === result.id ? result : item));
    } catch (error) { messages.error(error instanceof Error ? error.message : '操作失败'); }
  };
  return <section className="scan-tasks-page" aria-label="基线合规">{holder}
    <div className="page-heading">{!embedded ? <div><p className="eyebrow">SSH 无 Agent 只读检查</p><h1>Linux 基线与合规</h1></div> : null}<Space><Button aria-label="刷新基线任务" icon={<RefreshCw size={17} />} onClick={() => void load()} /><Button type="primary" icon={<Plus size={17} />} onClick={() => setOpen(true)}>新建检查</Button></Space></div>
    <Alert type="info" showIcon message="任务完成不等于全部合规" description="完成 100% 仅表示检查流程结束；合规率按实际检查项计算，反映通过比例。" />
    {loadError ? <Alert type="error" showIcon message="基线数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}><div hidden={Boolean(loadError)} className={`baselines-data-shell${loading ? ' is-loading' : ''}`}><div className="metric-band"><div><span>任务总数</span><strong>{summary.total}</strong></div><div><span>检查中</span><strong>{summary.running}</strong></div><div><span>最近一次已完成检查合规率</span><strong>{summary.rate}%</strong></div><div><span>失败任务</span><strong>{summary.failed}</strong></div></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={tasks} scroll={{ x: 1050 }} columns={[
      { title: '检查任务', render: (_, item: BaselineTask) => <div className="asset-identity"><span><ShieldCheck size={17} /></span><div><strong>{item.name}</strong><small>{item.asset_snapshot.map((asset) => `${asset.name} · ${asset.host}`).join('、')}</small></div></div> },
      { title: '策略版本', dataIndex: 'policy_version', width: 110 },
      { title: '状态', width: 150, render: (_, item: BaselineTask) => <div className="scan-progress"><Tag color={statusColor[item.status]}>{statusText[item.status]}</Tag><Progress percent={item.progress} size="small" showInfo={false} /></div> },
      { title: '合规率（检查项）', width: 130, render: (_, item: BaselineTask) => item.summary ? `${item.summary.compliance_rate}%` : '待检查' },
      { title: '执行边界', width: 90, render: () => <Tag color="green">只读</Tag> },
      { title: '操作', width: 190, align: 'right', render: (_, item: BaselineTask) => <Space>
        <Button type="text" aria-label={`查看 ${item.name}`} icon={<Eye size={17} />} onClick={() => setSelected(item)} />
        {item.status === 'queued' ? <Button type="text" aria-label={`启动 ${item.name}`} icon={<Play size={17} />} onClick={() => void act(item, 'start')} /> : null}
        {!terminal.includes(item.status) ? <Button type="text" danger aria-label={`取消 ${item.name}`} icon={<X size={17} />} onClick={() => void act(item, 'cancel')} /> : null}
        {terminal.includes(item.status) ? <Button type="text" aria-label={`重跑 ${item.name}`} icon={<Repeat2 size={17} />} onClick={() => void act(item, 'rerun')} /> : null}
      </Space> },
    ]} /></div></div></Spin>
    <Modal title="新建基线检查" open={open} onCancel={() => setOpen(false)} onOk={() => void create()} okText="创建任务"><Form form={form} layout="vertical"><Form.Item name="name" label="任务名称" rules={[{ required: true }, { min: 2 }]}><Input /></Form.Item><Form.Item name="asset_ids" label="目标服务器" rules={[{ required: true }]}><Select mode="multiple" options={assets.map((asset) => ({ value: asset.id, label: `${asset.name} · ${asset.host}` }))} /></Form.Item><Tag color="green">仅执行内置 SSH 只读检查，不下发修复命令</Tag></Form></Modal>
    <Drawer title="基线检查证据" width={760} open={selected !== null} onClose={() => setSelected(null)}>{selected?.results.map((asset) => <section key={asset.asset_id}><h3>{asset.asset_name}</h3>{asset.message ? <Tag color="red">{asset.message}</Tag> : <Table<BaselineCheck> rowKey="rule_id" pagination={false} dataSource={asset.checks} columns={[{ title: '检查项', dataIndex: 'title' }, { title: '级别', dataIndex: 'severity', width: 80 }, { title: '结果', dataIndex: 'status', width: 90, render: (value) => <Tag color={value === 'passed' ? 'green' : 'red'}>{value === 'passed' ? '通过' : '不通过'}</Tag> }, { title: '实际值', dataIndex: 'observed' }, { title: '期望值', dataIndex: 'expected' }]} />}</section>)}</Drawer>
  </section>;
}
