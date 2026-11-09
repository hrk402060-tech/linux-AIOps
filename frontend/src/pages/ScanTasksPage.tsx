import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Empty, Input, message, Popconfirm, Progress, Select, Space, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { AuthUser } from '../api/auth';
import { Archive, Ban, Download, Eye, Play, Plus, RefreshCw, Repeat2, RotateCcw, Search, ShieldCheck, Trash2 } from 'lucide-react';

import { getAssets } from '../api/assets';
import {
  archiveScanTask, cancelScanTask, createScanTask, downloadScanReport, getScanTasks,
  rerunScanTask, startScanTask,
} from '../api/scan-tasks';
import { ScanTaskCreateDrawer } from '../components/ScanTaskCreateDrawer';
import { ScanTaskDetailDrawer } from '../components/ScanTaskDetailDrawer';
import { ScanTaskBatchActions } from '../components/ScanTaskBatchActions';
import { ScanTaskRemoveModal } from '../components/ScanTaskRemoveModal';
import type { Asset } from '../types/asset';
import type { ScanStatus, ScanTask, ScanTaskCreate, ScanType } from '../types/scan-task';

const typeLabels: Record<ScanType, string> = {
  host_vulnerability: '主机漏洞', service_vulnerability: '服务漏洞', comprehensive: '综合扫描',
};
const statusLabels: Record<ScanStatus, string> = {
  queued: '排队', scanning: '扫描中', aggregating: '汇总', completed: '完成',
  failed: '失败', cancelled: '取消',
};
const statusColors: Record<ScanStatus, string> = {
  queued: 'blue', scanning: 'cyan', aggregating: 'gold', completed: 'green',
  failed: 'red', cancelled: 'default',
};
const terminal: ScanStatus[] = ['completed', 'failed', 'cancelled'];
const formatDate = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value));

export function ScanTasksPage({ currentUser = { role: 'auditor' } }: { currentUser?: Pick<AuthUser, 'role'> }) {
  const canWrite = currentUser.role === 'admin' || currentUser.role === 'operator';
  const canRemove = currentUser.role === 'admin';
  const [items, setItems] = useState<ScanTask[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<ScanStatus | 'all'>('all');
  const [scanType, setScanType] = useState<ScanType | 'all'>('all');
  const [archiveView, setArchiveView] = useState('active');
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<ScanTask | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [removeTasks, setRemoveTasks] = useState<ScanTask[] | null>(null);
  const mounted = useRef(true);
  const readGeneration = useRef(0);
  const mutationPending = useRef(false);
  const busy = batchBusy || submitting || actionId !== null;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; readGeneration.current += 1; }; }, []);
  useEffect(() => { setSelectedIds([]); }, [query, status, scanType, archiveView]);

  const load = useCallback(async (): Promise<boolean> => {
    const generation = ++readGeneration.current;
    setLoading(true);
    try {
      const [tasks, targets] = await Promise.all([getScanTasks(archiveView), getAssets('enabled')]);
      if (!mounted.current || generation !== readGeneration.current) return false;
      setItems(tasks); setAssets(targets);
      return true;
    } catch (error) {
      if (mounted.current && generation === readGeneration.current) messageApi.error(error instanceof Error ? error.message : '加载失败');
      return false;
    } finally { if (mounted.current && generation === readGeneration.current) setLoading(false); }
  }, [archiveView, messageApi]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (busy || loading || !items.some((item) => ['scanning', 'aggregating'].includes(item.status))) return;
    let stopped = false, pending = false;
    const timer = window.setInterval(async () => {
      if (pending || document.hidden) return;
      pending = true;
      const generation = readGeneration.current;
      try {
        const tasks = await getScanTasks(archiveView);
        if (!stopped && mounted.current && generation === readGeneration.current) setItems(tasks);
      } catch (error) {
        if (!stopped) { window.clearInterval(timer); messageApi.error(error instanceof Error ? error.message : '扫描状态刷新失败'); }
      } finally { pending = false; }
    }, 1500);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [items, archiveView, busy, loading, messageApi]);

  const setArchived = async (task: ScanTask, archived: boolean) => {
    if (!canWrite || busy || mutationPending.current) return;
    mutationPending.current = true; readGeneration.current += 1;
    setActionId(task.id);
    try {
      await archiveScanTask(task.id, archived);
      messageApi.success(archived ? '任务已归档，扫描证据保持不变' : '任务已恢复');
      await load();
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '归档操作失败'); }
    finally { mutationPending.current = false; if (mounted.current) setActionId(null); }
  };

  const filtered = useMemo(() => items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    const content = (item.name + ' ' + item.asset_snapshot.map((asset) => asset.name).join(' ')).toLowerCase();
    return (!keyword || content.includes(keyword)) && (status === 'all' || item.status === status)
      && (scanType === 'all' || item.scan_type === scanType);
  }), [items, query, scanType, status]);
  const selectedTasks = filtered.filter((item) => selectedIds.includes(item.id));
  const completeBatch = async (failedIds: string[]): Promise<boolean> => {
    setSelectedIds(failedIds);
    return load();
  };
  const changeBatchBusy = (value: boolean) => { readGeneration.current += 1; setBatchBusy(value); };
  const openRemove = (tasks: ScanTask[]) => {
    if (!canRemove || busy || loading || mutationPending.current) return;
    setRemoveTasks(tasks); changeBatchBusy(true);
  };

  const create = async (payload: ScanTaskCreate) => {
    if (!canWrite || busy || mutationPending.current) return;
    mutationPending.current = true; readGeneration.current += 1;
    setSubmitting(true);
    try { await createScanTask(payload); messageApi.success('任务已进入队列'); setCreateOpen(false); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '创建失败'); }
    finally { mutationPending.current = false; if (mounted.current) setSubmitting(false); }
  };
  const act = async (task: ScanTask, action: 'start' | 'cancel' | 'rerun') => {
    if (!canWrite || busy || mutationPending.current) return;
    mutationPending.current = true; readGeneration.current += 1;
    setActionId(task.id);
    try {
      const result = action === 'start' ? await startScanTask(task.id)
        : action === 'cancel' ? await cancelScanTask(task.id) : await rerunScanTask(task.id);
      messageApi.success(action === 'start' ? '扫描已启动' : action === 'cancel' ? '任务已取消' : '重跑任务已创建');
      setItems((current) => action === 'rerun' ? [result, ...current]
        : current.map((item) => item.id === result.id ? result : item));
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '操作失败'); }
    finally { mutationPending.current = false; if (mounted.current) setActionId(null); }
  };

  const columns: ColumnsType<ScanTask> = [
    { title: '扫描任务', key: 'identity', render: (_, item) => <div className="asset-identity"><span><ShieldCheck size={17} /></span><div><strong>{item.name}</strong><small>{item.asset_snapshot.map((asset) => asset.name).join('、')}</small></div></div> },
    { title: '类型', dataIndex: 'scan_type', width: 120, render: (value: ScanType) => typeLabels[value] },
    { title: '状态', dataIndex: 'status', width: 150, render: (value: ScanStatus, item) => <div className="scan-progress"><Tag color={statusColors[value]}>{statusLabels[value]}</Tag><Progress percent={item.progress} size="small" showInfo={false} /></div> },
    { title: '资产', key: 'assets', width: 80, render: (_, item) => item.asset_ids.length + ' 台' },
    { title: '知识版本', dataIndex: 'knowledge_version', width: 145, render: (value: string | null) => value ?? <span className="muted">未发布</span> },
    { title: '执行策略', key: 'policy', width: 100, render: () => <Tag color="green">只读</Tag> },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: formatDate },
    { title: '操作', key: 'actions', width: 210, align: 'right', render: (_, item) => <div className="row-actions">
      <Tooltip title="查看详情"><Button type="text" aria-label={'查看 ' + item.name} icon={<Eye size={17} />} onClick={() => setSelected(item)} /></Tooltip>
      {canWrite && !item.archived && item.status === 'queued' ? <Tooltip title="启动"><Button type="text" disabled={busy || loading} aria-label={'启动 ' + item.name} loading={actionId === item.id} icon={<Play size={17} />} onClick={() => void act(item, 'start')} /></Tooltip> : null}
      {canWrite && !terminal.includes(item.status) ? <Popconfirm title="取消扫描任务" okText="取消任务" cancelText="返回" onConfirm={() => void act(item, 'cancel')}><Tooltip title="取消"><Button type="text" disabled={busy || loading} danger aria-label={'取消 ' + item.name} icon={<Ban size={17} />} /></Tooltip></Popconfirm> : null}
      {canWrite && terminal.includes(item.status) ? <Tooltip title="重跑"><Button type="text" disabled={busy || loading} aria-label={'重跑 ' + item.name} loading={actionId === item.id} icon={<Repeat2 size={17} />} onClick={() => void act(item, 'rerun')} /></Tooltip> : null}
      {item.status === 'completed' ? <Tooltip title="下载报告"><Button type="text" aria-label={'下载 ' + item.name} icon={<Download size={17} />} onClick={() => void downloadScanReport(item.id).catch(() => messageApi.error('报告下载失败'))} /></Tooltip> : null}
      {canWrite && (item.archived ? <Tooltip title="恢复"><Button type="text" disabled={busy || loading} aria-label={'恢复 ' + item.name} loading={actionId === item.id} icon={<RotateCcw size={17} />} onClick={() => void setArchived(item, false)} /></Tooltip> : terminal.includes(item.status) ? <Tooltip title="归档，任务证据保留"><Button type="text" disabled={busy || loading} aria-label={'归档 ' + item.name} icon={<Archive size={17} />} onClick={() => void setArchived(item, true)} /></Tooltip> : null)}
      {canRemove && terminal.includes(item.status) ? <Tooltip title="删除任务，历史证据保留"><Button type="text" danger disabled={busy || loading} aria-label={'删除 ' + item.name} icon={<Trash2 size={17} />} onClick={() => openRemove([item])} /></Tooltip> : null}
    </div> },
  ];

  return <section className="scan-tasks-page">{contextHolder}
    <div className="page-heading"><div><p className="eyebrow">只读检测编排</p><h1>扫描任务</h1><Space wrap className="muted" aria-label="任务统计" size={12}><span>共 {items.length} 项</span><span>运行 {items.filter((item) => ['scanning', 'aggregating'].includes(item.status)).length}</span><span>完成 {items.filter((item) => item.status === 'completed').length}</span><span>失败 / 取消 {items.filter((item) => ['failed', 'cancelled'].includes(item.status)).length}</span></Space></div>{canWrite ? <Button type="primary" disabled={busy || loading} icon={<Plus size={17} />} onClick={() => setCreateOpen(true)}>新建任务</Button> : null}</div>
    <div className="scan-toolbar"><Input disabled={busy} allowClear prefix={<Search size={16} />} placeholder="搜索任务或目标资产" value={query} onChange={(event) => setQuery(event.target.value)} /><Select aria-label="任务归档筛选" disabled={busy} value={archiveView} onChange={setArchiveView} options={[{ value: 'active', label: '当前任务' }, { value: 'archived', label: '已归档' }, { value: 'all', label: '全部任务' }]} /><Select disabled={busy} value={scanType} onChange={setScanType} options={[{ value: 'all', label: '全部类型' }, ...Object.entries(typeLabels).map(([value, label]) => ({ value, label }))]} /><Select disabled={busy} value={status} onChange={setStatus} options={[{ value: 'all', label: '全部状态' }, ...Object.entries(statusLabels).map(([value, label]) => ({ value, label }))]} /><Tooltip title="刷新"><Button aria-label="刷新" disabled={busy} icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip></div>
    {canWrite ? <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 12 }}>
      {selectedTasks.length ? <Space wrap><strong>已选 {selectedTasks.length} 项</strong>
        <Tooltip title="选择当前筛选的全部任务，最多 100 项"><Button aria-label="全选筛选结果" disabled={busy || loading || !filtered.length || filtered.length > 100} onClick={() => setSelectedIds(filtered.map((item) => item.id))}>全选筛选结果</Button></Tooltip>
        <Button disabled={busy || !selectedTasks.length} onClick={() => setSelectedIds([])}>清空选择</Button>
      </Space> : null}<ScanTaskBatchActions tasks={selectedTasks} disabled={busy || loading} onComplete={completeBatch}
        onBusyChange={changeBatchBusy} />
      {canRemove && selectedTasks.length ? <Tooltip title={`可删除 ${selectedTasks.filter((item) => terminal.includes(item.status)).length} 项`}><Button danger aria-label="批量删除" icon={<Trash2 size={16} />} disabled={busy || loading || !selectedTasks.some((item) => terminal.includes(item.status))} onClick={() => openRemove(selectedTasks)}>删除</Button></Tooltip> : null}
    </div> : null}
    <ScanTaskRemoveModal tasks={removeTasks ?? []} open={removeTasks !== null} onClose={() => setRemoveTasks(null)}
      onComplete={completeBatch} onBusyChange={changeBatchBusy} />
    <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={filtered} loading={loading}
      rowSelection={canWrite ? { selectedRowKeys: selectedTasks.map((item) => item.id), preserveSelectedRowKeys: true,
        getCheckboxProps: () => ({ disabled: busy || loading }),
        onChange: (keys) => { if (keys.length <= 100) setSelectedIds(keys.map(String)); else messageApi.warning('每次最多选择 100 项'); },
      } : undefined}
      pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1180 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无扫描任务" /> }} /></div>
    <ScanTaskCreateDrawer open={createOpen} assets={assets} submitting={submitting} onClose={() => setCreateOpen(false)} onSubmit={create} />
    <ScanTaskDetailDrawer task={selected} onClose={() => setSelected(null)} />
  </section>;
}
