import { useEffect, useRef, useState } from 'react';
import { Alert, Input, Modal, Typography, message } from 'antd';
import {
  ExternalAlertRequestError, analyzeExternalAlert, cancelDiagnosticJob,
  type DiagnosticJob, type ExternalAlert,
} from '../../api/externalAlerts';

export type DiagnosticJobAction = { kind: 'enqueue'; alert: ExternalAlert }
  | { kind: 'cancel'; alert: ExternalAlert; job: DiagnosticJob };

export function DiagnosticJobActionModal({ target, onClose, onUpdated }: {
  target: DiagnosticJobAction | null; onClose: () => void; onUpdated: () => Promise<void>;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const generation = useRef(0);
  const [messages, holder] = message.useMessage();
  const expected = target?.kind === 'cancel' ? '取消只读诊断' : '提交只读诊断';
  const targetKey = target ? `${target.kind}:${target.alert.id}:${target.kind === 'cancel' ? target.job.id : ''}` : '';
  const trimmedReason = reason.trim();
  const valid = Boolean(target && confirmation.trim() === expected && trimmedReason.length >= 3 && trimmedReason.length <= 1000);
  useEffect(() => {
    generation.current += 1;
    setConfirmation(''); setReason('');
    return () => { generation.current += 1; };
  }, [targetKey, target?.kind]);
  const submit = async () => {
    if (!target || !valid || busy.current) return;
    busy.current = true; setSaving(true);
    const requestGeneration = generation.current;
    try {
      const payload = { confirmation: target.alert.id, reason: trimmedReason };
      const result = target.kind === 'enqueue'
        ? await analyzeExternalAlert(target.alert.id, payload)
        : await cancelDiagnosticJob(target.alert.id, target.job.id, payload);
      if (requestGeneration !== generation.current) return;
      if (target.kind === 'cancel') messages.info(result.status === 'cancelled'
        ? '任务已取消' : '取消请求已登记，等待当前只读连接结束');
      else messages.info(result.status === 'failed' ? '任务未启动，请查看失败原因和通知记录' : '只读诊断已入队，不代表根因或恢复已验证');
      onClose(); await onUpdated();
    } catch (error) {
      if (requestGeneration !== generation.current) return;
      messages.error(error instanceof Error ? error.message : '诊断任务操作失败');
      if (error instanceof ExternalAlertRequestError && [403, 409].includes(error.status)) {
        onClose(); await onUpdated();
      }
    } finally { busy.current = false; setSaving(false); }
  };
  return <>{holder}<Modal
    title={target?.kind === 'cancel' ? '人工取消只读诊断' : '人工提交只读诊断'}
    open={target !== null} destroyOnHidden confirmLoading={saving}
    onCancel={() => { if (!busy.current) onClose(); }} onOk={() => void submit()}
    okText={target?.kind === 'cancel' ? '确认取消' : '确认入队'}
    okButtonProps={{ disabled: !valid || saving }} cancelButtonProps={{ disabled: saving }}
  >
    <Typography.Paragraph>{target?.alert.title} · {target?.alert.asset_name}</Typography.Paragraph>
    {target?.kind === 'cancel' ? <Typography.Paragraph>取消任务：<Typography.Text code>{target.job.id}</Typography.Text></Typography.Paragraph> : null}
    <Typography.Paragraph style={{ marginTop: 16 }}>请输入确认内容：<Typography.Text code>{expected}</Typography.Text></Typography.Paragraph>
    <Input aria-label="诊断确认文本" maxLength={80} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={saving} />
    <Typography.Paragraph style={{ marginTop: 16, marginBottom: 8 }}>操作原因</Typography.Paragraph>
    <Input.TextArea aria-label="诊断操作原因" maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} disabled={saving} rows={3} />
    <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
      请填写 3～1000 个字符，首尾空格不计入；操作原因将保留在审计记录中。
    </Typography.Paragraph>
  </Modal></>;
}
