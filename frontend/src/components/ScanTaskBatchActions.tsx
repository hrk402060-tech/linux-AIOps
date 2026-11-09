import { useEffect, useRef, useState } from 'react';
import { Alert, Button, List, Modal, Space, Tag, Tooltip } from 'antd';
import { Archive, Ban, Play, Repeat2, RotateCcw } from 'lucide-react';
import { archiveScanTask, cancelScanTask, rerunScanTask, ScanTaskRequestError, startScanTask } from '../api/scan-tasks';
import type { ScanTask } from '../types/scan-task';

type ScanAction = 'start' | 'cancel' | 'rerun' | 'archive' | 'restore';
const actions = [
  { key: 'start', label: '启动', icon: <Play size={16} /> },
  { key: 'cancel', label: '取消', icon: <Ban size={16} /> },
  { key: 'rerun', label: '重跑', icon: <Repeat2 size={16} /> },
  { key: 'archive', label: '归档', icon: <Archive size={16} /> },
  { key: 'restore', label: '恢复', icon: <RotateCcw size={16} /> },
] as const;
const statusLabels = { queued: '排队', scanning: '扫描中', aggregating: '汇总', completed: '完成', failed: '失败', cancelled: '取消' };
export function eligibleForScanAction(task: ScanTask, action: ScanAction): boolean {
  const terminal = ['completed', 'failed', 'cancelled'].includes(task.status);
  if (action === 'restore') return terminal && task.archived;
  if (action === 'archive') return terminal && !task.archived;
  if (action === 'rerun') return terminal;
  return !task.archived && (action === 'start' ? task.status === 'queued' : !terminal);
}
function execute(task: ScanTask, action: ScanAction): Promise<ScanTask> {
  if (action === 'start') return startScanTask(task.id);
  if (action === 'cancel') return cancelScanTask(task.id);
  if (action === 'rerun') return rerunScanTask(task.id);
  return archiveScanTask(task.id, action === 'archive');
}
interface Failure { id: string; name: string; message: string }
interface BatchTarget { action: ScanAction; label: string; items: ScanTask[]; skipped: number }

export function ScanTaskBatchActions({ tasks, disabled, onComplete, onBusyChange }: {
  tasks: ScanTask[]; disabled: boolean; onComplete: (failedIds: string[]) => Promise<boolean>;
  onBusyChange: (busy: boolean) => void;
}) {
  const [target, setTarget] = useState<BatchTarget | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<{ succeeded: number; failures: Failure[]; refreshFailed: boolean } | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const close = () => { if (!pending.current) { setTarget(null); onBusyChange(false); } };
  const confirm = async () => {
    if (pending.current || !target || !target.items.length || target.items.length > 100) return;
    pending.current = true; setRunning(true); setProgress(0); setResult(null);
    const failures: Failure[] = [];
    let succeeded = 0;
    try {
      for (const [index, task] of target.items.entries()) {
        if (!mounted.current) return;
        try { await execute(task, target.action); succeeded += 1; }
        catch (error) {
          const rejected = error instanceof ScanTaskRequestError;
          failures.push({ id: task.id, name: task.name, message: rejected ? error.message : '请求结果待核实，请刷新后确认；未自动重试' });
          if (!rejected || [401, 403].includes(error.status) || error.status >= 500) {
            failures.push(...target.items.slice(index + 1).map((item) => ({ id: item.id, name: item.name, message: '批量操作已中止，未执行' })));
            break;
          }
        }
        if (mounted.current) setProgress(index + 1);
      }
      if (!mounted.current) return;
      let refreshed = false;
      try { refreshed = await onComplete(failures.map((item) => item.id)); }
      catch (error) {
        failures.push({ id: 'refresh', name: '状态刷新', message: error instanceof Error ? error.message : '刷新失败，请手动核对状态' });
      }
      if (mounted.current) { setResult({ succeeded, failures, refreshFailed: !refreshed }); setTarget(null); }
    } finally {
      pending.current = false;
      if (mounted.current) { setRunning(false); onBusyChange(false); }
    }
  };
  return <>
    {tasks.length ? <Space wrap>
      {actions.map((action) => <Tooltip key={action.key} title={`可操作 ${tasks.filter((task) => eligibleForScanAction(task, action.key)).length} 项`}>
        <Button aria-label={'批量' + action.label} icon={action.icon}
          disabled={disabled || running || !tasks.length || tasks.length > 100 || !tasks.some((task) => eligibleForScanAction(task, action.key))}
          onClick={() => { setTarget({ action: action.key, label: action.label,
            items: tasks.filter((task) => eligibleForScanAction(task, action.key)),
            skipped: tasks.filter((task) => !eligibleForScanAction(task, action.key)).length }); onBusyChange(true); }}>
          {action.label}
        </Button>
      </Tooltip>)}
    </Space> : null}
    {result ? <Alert style={{ marginTop: 8, flexBasis: '100%' }} type={result.failures.length || result.refreshFailed ? 'warning' : 'success'} showIcon
      message={`成功 ${result.succeeded} 项，失败或未执行 ${result.failures.length} 项`}
      description={<>{result.refreshFailed ? <p>列表刷新失败，请刷新后核对状态，勿重复提交已成功任务。</p> : null}
        {result.failures.length ? <List size="small" dataSource={result.failures} renderItem={(item) => <List.Item key={item.id}>{item.name}：<span>{item.message}</span></List.Item>} /> : null}</>} /> : null}
    <Modal title={target ? '批量' + target.label : ''} open={target !== null} onCancel={close}
      onOk={() => void confirm()} okText={'确认' + (target?.label ?? '')} cancelText="返回"
      confirmLoading={running} cancelButtonProps={{ disabled: running }} maskClosable={!running} closable={!running} destroyOnHidden>
      {target ? <>
        <p>可操作 {target.items.length} 项，跳过 {target.skipped} 项</p>
        {target.action === 'archive' ? <Alert type="info" message="归档后移出当前列表，报告和漏洞证据保留，可在已归档中恢复。" /> : null}
        {target.action === 'rerun' ? <Alert type="info" message="创建新的排队任务，原记录保持不变；不会自动启动扫描。" /> : null}
        {running ? <p>已处理 {progress} / {target.items.length}</p> : null}
        <List style={{ maxHeight: 260, overflowY: 'auto' }} size="small" dataSource={target.items}
          renderItem={(item) => <List.Item key={item.id}>{item.name}<Tag>{statusLabels[item.status]}</Tag></List.Item>} />
      </> : null}
    </Modal>
  </>;
}
