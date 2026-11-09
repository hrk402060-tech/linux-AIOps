import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Alert, Input, List, Modal, Tag } from 'antd';
import { removeScanTask, ScanTaskRequestError } from '../api/scan-tasks';
import type { ScanTask } from '../types/scan-task';

const statusLabels = { completed: '完成', failed: '失败', cancelled: '取消' };
const terminal = (task: ScanTask) => ['completed', 'failed', 'cancelled'].includes(task.status);
interface Failure { id: string; name: string; message: string }
interface RemoveResult { succeeded: number; failures: Failure[]; refreshFailed: boolean; refreshError: string | null }
interface Props {
  tasks: ScanTask[]; open: boolean; onClose: () => void;
  onComplete: (failedIds: string[]) => Promise<boolean>; onBusyChange: (busy: boolean) => void;
}

export function ScanTaskRemoveModal({ tasks, open, onClose, onComplete, onBusyChange }: Props) {
  const [reason, setReason] = useState(''), [confirmation, setConfirmation] = useState('');
  const [running, setRunning] = useState(false), [progress, setProgress] = useState(0);
  const [result, setResult] = useState<RemoveResult | null>(null);
  const eligible = useMemo(() => tasks.filter(terminal), [tasks]);
  const expected = `删除 ${eligible.length} 项任务`;
  const reasonLength = reason.trim().length;
  const reasonError = reason.length === 0 ? '' : reasonLength < 3 ? '删除原因不足 3 个字符，请补充说明。'
    : reasonLength > 1000 ? '删除原因不能超过 1000 个字符。' : '';
  const confirmationError = confirmation.length > 0 && confirmation.trim() !== expected
    ? `确认文本不匹配，请输入“${expected}”。` : '';
  const valid = eligible.length > 0 && tasks.length <= 100 && confirmation.trim() === expected
    && reasonLength >= 3 && reasonLength <= 1000;
  const pending = useRef(false), mounted = useRef(true);
  const fieldId = useId();
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (open) { setReason(''); setConfirmation(''); setProgress(0); setResult(null); } }, [open]);
  const close = () => { if (!pending.current) { onClose(); onBusyChange(false); } };
  const confirm = async () => {
    if (!open || pending.current || !valid) return;
    pending.current = true; setRunning(true); setProgress(0); setResult(null); onBusyChange(true);
    const target = [...eligible], skipped = tasks.filter((task) => !terminal(task)).map((task) => task.id);
    const failures: Failure[] = [];
    let succeeded = 0;
    try {
      for (const [index, task] of target.entries()) {
        if (!mounted.current) return;
        try { await removeScanTask(task.id, reason); succeeded += 1; }
        catch (error) {
          const rejected = error instanceof ScanTaskRequestError;
          failures.push({ id: task.id, name: task.name,
            message: rejected ? error.message : '请求结果待核实，请刷新后确认；未自动重试' });
          if (!rejected || [401, 403].includes(error.status) || error.status >= 500) {
            failures.push(...target.slice(index + 1).map((item) => ({ id: item.id, name: item.name, message: '批量删除已中止，未执行' })));
            break;
          }
        }
        if (mounted.current) setProgress(index + 1);
      }
      if (!mounted.current) return;
      let refreshed = false;
      let refreshError: string | null = null;
      try { refreshed = await onComplete([...failures.map((item) => item.id), ...skipped]); }
      catch (error) { refreshError = error instanceof Error ? error.message : '状态刷新失败'; }
      if (mounted.current) { setResult({ succeeded, failures, refreshFailed: !refreshed, refreshError }); onClose(); }
    } finally {
      pending.current = false;
      if (mounted.current) { setRunning(false); onBusyChange(false); }
    }
  };
  return <>
    {result ? <Alert style={{ marginBottom: 12 }} showIcon type={result.failures.length || result.refreshFailed ? 'warning' : 'success'}
      message={`已删除 ${result.succeeded} 项，失败或未执行 ${result.failures.length} 项`}
      description={<>{result.refreshFailed ? <p>列表刷新失败，请刷新后核对状态，勿重复提交已成功任务。</p> : null}
        {result.refreshError ? <p>{result.refreshError}</p> : null}
        {result.failures.length ? <List size="small" dataSource={result.failures}
          renderItem={(item) => <List.Item key={item.id}>{item.name}：<span>{item.message}</span></List.Item>} /> : null}</>} /> : null}
    <Modal title="删除扫描任务" open={open} onCancel={close} onOk={() => void confirm()}
      okText="确认删除" cancelText="返回" confirmLoading={running}
      okButtonProps={{ danger: true, disabled: !valid }} cancelButtonProps={{ disabled: running }}
      closable={!running} maskClosable={!running} keyboard={!running} destroyOnHidden>
      <Alert type="warning" showIcon message="任务将从任务列表移除，无法恢复。漏洞、报告和历史证据保留。" />
      <p>可删除 {eligible.length} 项，跳过 {tasks.length - eligible.length} 项</p>
      {tasks.length > 100 ? <p role="alert">每次最多选择 100 项</p> : null}
      <List size="small" style={{ maxHeight: 200, overflowY: 'auto' }} dataSource={eligible}
        renderItem={(task) => <List.Item key={task.id}>{task.name}<Tag>{statusLabels[task.status as keyof typeof statusLabels]}</Tag></List.Item>} />
      <p><label htmlFor={fieldId + '-reason'}>删除原因</label></p>
      <Input.TextArea id={fieldId + '-reason'} value={reason} disabled={running} maxLength={1000} rows={3}
        status={reasonError ? 'error' : undefined} aria-invalid={Boolean(reasonError)}
        aria-describedby={`${fieldId}-reason-hint${reasonError ? ` ${fieldId}-reason-error` : ''}`}
        onChange={(event) => setReason(event.target.value)} placeholder="请输入至少 3 个字符的删除原因" />
      <p id={fieldId + '-reason-hint'}>删除原因需 3 至 1000 个字符，首尾空格不计入。</p>
      {reasonError ? <p id={fieldId + '-reason-error'} role="alert">{reasonError}</p> : null}
      <p><label htmlFor={fieldId + '-confirmation'}>确认文本</label>：<strong>{expected}</strong></p>
      <Input id={fieldId + '-confirmation'} value={confirmation} disabled={running}
        status={confirmationError ? 'error' : undefined} aria-invalid={Boolean(confirmationError)}
        aria-describedby={confirmationError ? fieldId + '-confirmation-error' : undefined}
        onChange={(event) => setConfirmation(event.target.value)} placeholder={`请输入“${expected}”`} autoComplete="off" />
      {confirmationError ? <p id={fieldId + '-confirmation-error'} role="alert">{confirmationError}</p> : null}
      {running ? <p>已处理 {progress} / {eligible.length}</p> : null}
    </Modal>
  </>;
}
