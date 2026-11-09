import { useEffect, useMemo, useState } from 'react';
import { Button, Drawer, Form, Input, Modal, Progress, Select, Space, Table, Tag, message } from 'antd';
import { Eye, KeyRound, Play, Plus, RefreshCw, Repeat2, X } from 'lucide-react';

import { getCredentials } from '../api/credentials';
import { cancelCredentialRiskTask, createCredentialRiskTask, getCredentialRiskTasks, rerunCredentialRiskTask, startCredentialRiskTask } from '../api/credential-risks';
import type { Credential } from '../types/credential';
import type { CredentialRiskResult, CredentialRiskStatus, CredentialRiskTask } from '../types/credential-risk';

const statusText: Record<CredentialRiskStatus, string> = { queued: '排队', assessing: '评估中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' };
const statusColor: Record<CredentialRiskStatus, string> = { queued: 'blue', assessing: 'cyan', aggregating: 'gold', completed: 'green', failed: 'red', cancelled: 'default' };
const violationText: Record<string, string> = { minimum_length: '长度不足 12 位', character_variety: '字符类别不足 3 种', common_password: '命中常见弱口令', contains_username: '包含用户名', repeated_characters: '连续重复字符' };
const terminal: CredentialRiskStatus[] = ['completed', 'failed', 'cancelled'];

export function CredentialRisksPage({ embedded = false }: { embedded?: boolean }) {
  const [tasks, setTasks] = useState<CredentialRiskTask[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [selected, setSelected] = useState<CredentialRiskTask | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ name: string; credential_ids: string[] }>();
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    try { const [items, secrets] = await Promise.all([getCredentialRiskTasks(), getCredentials()]); setTasks(items); setCredentials(secrets.filter((item) => item.credential_type === 'password')); }
    catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const summary = useMemo(() => ({ total: tasks.length, running: tasks.filter((item) => ['assessing', 'aggregating'].includes(item.status)).length, atRisk: tasks.find((item) => item.status === 'completed')?.summary?.at_risk ?? 0, high: tasks.find((item) => item.status === 'completed')?.summary?.high ?? 0 }), [tasks]);
  const create = async () => {
    const values = await form.validateFields();
    try { await createCredentialRiskTask(values.name, values.credential_ids); setOpen(false); form.resetFields(); await load(); }
    catch (error) { messages.error(error instanceof Error ? error.message : '创建失败'); }
  };
  const act = async (task: CredentialRiskTask, action: 'start' | 'cancel' | 'rerun') => {
    try {
      const result = action === 'start' ? await startCredentialRiskTask(task.id) : action === 'cancel' ? await cancelCredentialRiskTask(task.id) : await rerunCredentialRiskTask(task.id);
      setTasks((current) => action === 'rerun' ? [result, ...current] : current.map((item) => item.id === result.id ? result : item));
    } catch (error) { messages.error(error instanceof Error ? error.message : '操作失败'); }
  };
  return <section className="scan-tasks-page" aria-label="弱口令评估">{holder}
    <div className="page-heading">{!embedded ? <div><p className="eyebrow">后端内存离线策略评估</p><h1>弱口令风险</h1></div> : null}<Space><Button aria-label="刷新弱口令任务" icon={<RefreshCw size={17} />} onClick={() => void load()} /><Button type="primary" icon={<Plus size={17} />} onClick={() => setOpen(true)}>新建评估</Button></Space></div>
    <div className="metric-band"><div><span>任务总数</span><strong>{summary.total}</strong></div><div><span>评估中</span><strong>{summary.running}</strong></div><div><span>最近风险凭据</span><strong>{summary.atRisk}</strong></div><div><span>高风险</span><strong>{summary.high}</strong></div></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={tasks} scroll={{ x: 1050 }} columns={[
      { title: '评估任务', render: (_, item: CredentialRiskTask) => <div className="asset-identity"><span><KeyRound size={17} /></span><div><strong>{item.name}</strong><small>{item.credential_snapshot.map((credential) => `${credential.name} · ${credential.username}`).join('、')}</small></div></div> },
      { title: '策略版本', dataIndex: 'policy_version', width: 110 },
      { title: '状态', width: 150, render: (_, item: CredentialRiskTask) => <div className="scan-progress"><Tag color={statusColor[item.status]}>{statusText[item.status]}</Tag><Progress percent={item.progress} size="small" showInfo={false} /></div> },
      { title: '风险 / 高风险', width: 130, render: (_, item: CredentialRiskTask) => item.summary ? `${item.summary.at_risk} / ${item.summary.high}` : '待评估' },
      { title: '执行边界', width: 100, render: () => <Tag color="green">离线评估</Tag> },
      { title: '操作', width: 190, align: 'right', render: (_, item: CredentialRiskTask) => <Space><Button type="text" aria-label={`查看 ${item.name}`} icon={<Eye size={17} />} onClick={() => setSelected(item)} />{item.status === 'queued' ? <Button type="text" aria-label={`启动 ${item.name}`} icon={<Play size={17} />} onClick={() => void act(item, 'start')} /> : null}{!terminal.includes(item.status) ? <Button type="text" danger aria-label={`取消 ${item.name}`} icon={<X size={17} />} onClick={() => void act(item, 'cancel')} /> : null}{terminal.includes(item.status) ? <Button type="text" aria-label={`重跑 ${item.name}`} icon={<Repeat2 size={17} />} onClick={() => void act(item, 'rerun')} /> : null}</Space> },
    ]} /></div>
    <Modal title="新建弱口令风险评估" open={open} onCancel={() => setOpen(false)} onOk={() => void create()} okText="创建任务"><Form form={form} layout="vertical"><Form.Item name="name" label="任务名称" rules={[{ required: true }, { min: 2 }]}><Input /></Form.Item><Form.Item name="credential_ids" label="密码型凭据" rules={[{ required: true }]}><Select mode="multiple" options={credentials.map((item) => ({ value: item.id, label: `${item.name} · ${item.username} · ${item.secret_mask}` }))} /></Form.Item><Tag color="green">不发起登录尝试，不保存或回传密码内容</Tag></Form></Modal>
    <Drawer title="弱口令风险证据" width={760} open={selected !== null} onClose={() => setSelected(null)}><Table<CredentialRiskResult> rowKey="credential_id" pagination={false} dataSource={selected?.results ?? []} columns={[{ title: '凭据', render: (_, item) => <><strong>{item.credential_name}</strong><br /><span className="muted">{item.username}</span></> }, { title: '风险', dataIndex: 'risk_level', width: 90, render: (value) => <Tag color={value === 'high' ? 'red' : value === 'medium' ? 'gold' : 'green'}>{value === 'high' ? '高' : value === 'medium' ? '中' : '低'}</Tag> }, { title: '结论', dataIndex: 'status', width: 100, render: (value) => value === 'passed' ? '通过' : '存在风险' }, { title: '规则证据', dataIndex: 'violations', render: (values: string[]) => values.length ? values.map((value) => <Tag key={value}>{violationText[value] ?? value}</Tag>) : '未触发风险规则' }]} /></Drawer>
  </section>;
}
