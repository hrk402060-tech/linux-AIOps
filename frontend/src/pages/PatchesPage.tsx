import { useEffect, useId, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Space, Table, Tag, message } from 'antd';
import { Download, RefreshCw, ShieldCheck } from 'lucide-react';
import { approvePatchAdvice, executePatchAdvice, getPatchAdvice, type PatchAdvice } from '../api/patches';
import { vulnerabilitySeverityColors, vulnerabilitySeverityLabels, type VulnerabilitySeverity } from '../types/vulnerability';

const approvalPhrase = '确认补丁建议';
const executionPhrase = '确认更新软件包';
const defaultReason = '已完成变更，等待重新扫描验证';
type PatchAction = 'approve' | 'execute';
const patchStatusLabels: Record<PatchAdvice['status'], string> = {
  pending_approval: '待人工确认', pending_verification: '待复扫验证',
  fixed: '已修复', ignored: '已忽略', risk_accepted: '风险接受',
  invalid_target: '目标版本异常', manual_confirmation: '需人工核对',
};

function canApproveAdvice(item: PatchAdvice): boolean {
  return item.status === 'pending_approval' && item.target_version_relation === 'higher_than_current'
    && !item.target_version_issue;
}

function displayedStatus(item: PatchAdvice): PatchAdvice['status'] {
  if (item.status !== 'pending_approval' || canApproveAdvice(item)) return item.status;
  return ['lower_than_current', 'equal_to_current'].includes(item.target_version_relation ?? '')
    ? 'invalid_target' : 'manual_confirmation';
}

function versionIssue(item: PatchAdvice): string | null {
  if (item.target_version_issue) return item.target_version_issue;
  if (!['pending_approval', 'manual_confirmation', 'invalid_target'].includes(item.status)) return null;
  if (item.target_version_relation === 'lower_than_current') return '目标版本低于当前版本，禁止确认降级建议';
  if (item.target_version_relation === 'equal_to_current') return '目标版本与当前版本相同，不能作为升级建议';
  if (item.status === 'manual_confirmation') return '厂商修复证据不足，请人工核对漏洞判定';
  return item.target_version_relation === 'higher_than_current' ? null : '版本比较证据不足，请人工核对厂商公告';
}

export function PatchesPage() {
  const [items, setItems] = useState<PatchAdvice[]>([]);
  const [loading, setLoading] = useState(false);
  const [selection, setSelection] = useState<{ item: PatchAdvice; action: PatchAction } | null>(null);
  const [reason, setReason] = useState(defaultReason);
  const [confirmation, setConfirmation] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false), fieldId = useId();
  const target = selection?.item;
  const executing = selection?.action === 'execute';
  const phrase = executing ? executionPhrase : approvalPhrase;
  const reasonLabel = executing ? '更新原因' : '审批原因';
  const reasonLength = reason.trim().length;
  const reasonError = reason.length === 0 ? '' : reasonLength < 2 ? `${reasonLabel}不足 2 个字符，请补充说明。`
    : reasonLength > 1000 ? `${reasonLabel}不能超过 1000 个字符。` : '';
  const confirmationError = confirmation.length > 0 && confirmation.trim() !== phrase
    ? `确认文本不匹配，请输入“${phrase}”。` : '';
  const valid = Boolean(target && (executing ? target.execution_enabled :
    canApproveAdvice(target) && target.approval_phrase === `APPROVE ${target.id}`)
    && reasonLength >= 2 && reasonLength <= 1000 && confirmation.trim() === phrase);
  const [messages, holder] = message.useMessage();
  const load = async () => { setLoading(true); try { setItems(await getPatchAdvice()); } catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const openAction = (item: PatchAdvice, action: PatchAction) => {
    if (pending.current || !(action === 'execute' ? item.execution_enabled : canApproveAdvice(item))) return;
    setSelection({ item, action }); setReason(action === 'execute' ? '' : defaultReason); setConfirmation('');
  };
  const submit = async () => {
    if (!target || !valid || pending.current) return;
    pending.current = true; setSubmitting(true);
    try {
      if (executing) {
        await executePatchAdvice(target.id, reason.trim(), confirmation.trim());
        messages.success('软件包已更新，等待重新扫描验证');
      } else {
        await approvePatchAdvice(target.id, reason.trim(), target.approval_phrase);
        messages.success('已人工确认，等待重新扫描验证');
      }
      setSelection(null); await load();
    } catch (error) {
      messages.error(error instanceof Error ? error.message : executing ? '补丁更新失败' : '审批失败');
      if (executing) { setSelection(null); await load(); }
    } finally { pending.current = false; setSubmitting(false); }
  };
  return <section className="scan-tasks-page">{holder}
    <div className="page-heading"><div><p className="eyebrow">厂商公告关联建议</p><h1>补丁与变更中心</h1></div><Button aria-label="刷新补丁建议" icon={<RefreshCw size={17} />} onClick={() => void load()} /></div>
    <div className="metric-band"><div><span>待人工确认</span><strong>{items.filter(canApproveAdvice).length}</strong></div><div><span>待确认高危</span><strong>{items.filter((item) => canApproveAdvice(item) && ['critical', 'high'].includes(item.severity)).length}</strong></div><div><span>支持自动更新</span><strong>{loading && !items.length ? '—' : items.filter((item) => item.execution_enabled).length}</strong></div><div><span>验证方式</span><strong>重新扫描</strong></div></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={items} scroll={{ x: 1480 }} locale={{ emptyText: <Empty description="暂无补丁建议" /> }} columns={[
      { title: '资产 / CVE', render: (_, item) => <div className="asset-identity"><span><ShieldCheck size={17} /></span><div><strong>{item.asset_name}</strong><small>{item.asset_id} · {item.cve_id}</small></div></div> },
      { title: '软件包', dataIndex: 'package_name' },
      { title: '当前版本', dataIndex: 'current_version' },
      { title: '目标版本', render: (_, item) => <div className="stacked-cell"><strong>{item.target_version}</strong>{versionIssue(item) ? <small className="danger-text">{versionIssue(item)}</small> : null}</div> },
      { title: '回滚预案', dataIndex: 'rollback_plan', width: 300, render: (value: string) => <span className="stacked-cell">{value}</span> },
      { title: '级别', dataIndex: 'severity', render: (value: string) => <Tag color={vulnerabilitySeverityColors[value as VulnerabilitySeverity] ?? 'default'}>{vulnerabilitySeverityLabels[value as VulnerabilitySeverity] ?? '未知'}</Tag> },
      { title: '来源', dataIndex: 'source_name' },
      { title: '历史扫描', dataIndex: 'history_count', render: (value: number) => `${value} 次` },
      { title: '状态', render: (_, item) => <Tag color={displayedStatus(item) === 'invalid_target' ? 'red' : item.status === 'pending_verification' ? 'blue' : item.status === 'fixed' ? 'green' : 'gold'}>{patchStatusLabels[displayedStatus(item)]}</Tag> },
      { title: '操作', width: 216, render: (_, item) => <Space wrap size={6}>
        {item.execution_enabled ? <Button size="small" type="primary" icon={<Download size={14} />} disabled={submitting} onClick={() => openAction(item, 'execute')}>自动更新</Button> : null}
        <Button size="small" disabled={!canApproveAdvice(item) || submitting} onClick={() => openAction(item, 'approve')}>确认建议</Button>
        {!item.execution_enabled && item.execution_issue ? <small>{item.execution_issue}</small> : null}
      </Space> },
    ]} /></div>
    <Modal title={executing ? '自动更新软件包' : '人工确认补丁建议'} open={selection !== null}
      onCancel={() => { if (!pending.current) setSelection(null); }} onOk={() => void submit()}
      okText={executing ? '确认并更新' : '确认并等待验证'} cancelText="取消" confirmLoading={submitting}
      okButtonProps={{ disabled: submitting || !valid, danger: executing }}
      cancelButtonProps={{ disabled: submitting }} closable={!submitting} maskClosable={!submitting} keyboard={!submitting} destroyOnHidden>
      {target ? <>
        {executing ? <Alert type="warning" showIcon message="将更新所选服务器上的软件包"
          description="仅从该服务器已启用的 YUM / DNF 仓库安装目标版本。依赖包可能随之更新，服务可能受影响。平台不会自动重启服务器，更新后需重新扫描验证。" />
          : <p>仅记录人工确认，不会安装或执行补丁，操作人使用当前登录账号。</p>}
        <p>确认对象：<strong>{target.asset_name}</strong> · <span>{target.cve_id}</span> · <span>{target.package_name}</span></p>
        <p>版本：{target.current_version} → {target.target_version}</p>
        {!executing && target.target_version_issue ? <Alert type="error" showIcon message="补丁建议已阻断" description={target.target_version_issue} /> : null}
        <p className="confirmation-prompt">请输入确认文本：<strong>{phrase}</strong></p>
        <Input aria-label="确认文本" value={confirmation} disabled={submitting} autoComplete="off"
          status={confirmationError ? 'error' : undefined} aria-invalid={Boolean(confirmationError)}
          aria-describedby={confirmationError ? fieldId + '-confirmation-error' : undefined}
          onChange={(event) => setConfirmation(event.target.value)} placeholder={`请输入“${phrase}”`} />
        {confirmationError ? <p id={fieldId + '-confirmation-error'} role="alert">{confirmationError}</p> : null}
        <Input.TextArea aria-label={reasonLabel} value={reason} disabled={submitting} maxLength={1000}
          status={reasonError ? 'error' : undefined} aria-invalid={Boolean(reasonError)}
          aria-describedby={`${fieldId}-reason-hint${reasonError ? ` ${fieldId}-reason-error` : ''}`}
          onChange={(event) => setReason(event.target.value)} rows={2} style={{ marginTop: 12 }} />
        <p id={fieldId + '-reason-hint'}>{reasonLabel}需 2 至 1000 个字符，首尾空格不计入。</p>
        {reasonError ? <p id={fieldId + '-reason-error'} role="alert">{reasonError}</p> : null}
        {!executing && target.approval_phrase !== `APPROVE ${target.id}` ? <p role="alert">确认信息无效，请关闭后刷新补丁建议。</p> : null}
      </> : null}
    </Modal>
  </section>;
}
