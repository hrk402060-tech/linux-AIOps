import { useEffect, useId, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Table, Tag, message } from 'antd';
import { RefreshCw, ShieldCheck } from 'lucide-react';
import { approvePatchAdvice, getPatchAdvice, type PatchAdvice } from '../api/patches';
import { vulnerabilitySeverityColors, vulnerabilitySeverityLabels, type VulnerabilitySeverity } from '../types/vulnerability';

const approvalPhrase = '确认补丁建议';
const defaultReason = '已完成变更，等待重新扫描验证';
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
  const [target, setTarget] = useState<PatchAdvice | null>(null);
  const [reason, setReason] = useState(defaultReason);
  const [confirmation, setConfirmation] = useState('');
  const [approving, setApproving] = useState(false);
  const pending = useRef(false), fieldId = useId();
  const reasonLength = reason.trim().length;
  const reasonError = reason.length === 0 ? '' : reasonLength < 2 ? '审批原因不足 2 个字符，请补充说明。'
    : reasonLength > 1000 ? '审批原因不能超过 1000 个字符。' : '';
  const confirmationError = confirmation.length > 0 && confirmation.trim() !== approvalPhrase
    ? `确认文本不匹配，请输入“${approvalPhrase}”。` : '';
  const valid = Boolean(target && canApproveAdvice(target) && target.approval_phrase === `APPROVE ${target.id}`
    && reasonLength >= 2 && reasonLength <= 1000 && confirmation.trim() === approvalPhrase);
  const [messages, holder] = message.useMessage();
  const load = async () => { setLoading(true); try { setItems(await getPatchAdvice()); } catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  const openApproval = (item: PatchAdvice) => {
    if (pending.current || !canApproveAdvice(item)) return;
    setTarget(item); setReason(defaultReason); setConfirmation('');
  };
  const closeApproval = () => {
    if (pending.current) return;
    setTarget(null); setReason(defaultReason); setConfirmation('');
  };
  const approve = async () => {
    if (!target || !valid || pending.current) return;
    pending.current = true; setApproving(true);
    try {
      await approvePatchAdvice(target.id, reason.trim(), target.approval_phrase);
      messages.success('已人工确认，等待重新扫描验证');
      setTarget(null); setReason(defaultReason); setConfirmation(''); await load();
    } catch (error) { messages.error(error instanceof Error ? error.message : '审批失败'); }
    finally { pending.current = false; setApproving(false); }
  };
  return <section className="scan-tasks-page">{holder}
    <div className="page-heading"><div><p className="eyebrow">厂商公告关联建议</p><h1>补丁与变更中心</h1></div><Button aria-label="刷新补丁建议" icon={<RefreshCw size={17} />} onClick={() => void load()} /></div>
    <div className="metric-band"><div><span>待人工确认</span><strong>{items.filter(canApproveAdvice).length}</strong></div><div><span>待确认高危</span><strong>{items.filter((item) => canApproveAdvice(item) && ['critical', 'high'].includes(item.severity)).length}</strong></div><div><span>执行状态</span><strong>关闭</strong></div><div><span>验证方式</span><strong>重新扫描</strong></div></div>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={items} scroll={{ x: 1480 }} locale={{ emptyText: <Empty description="暂无补丁建议" /> }} columns={[{ title: '资产 / CVE', render: (_, item) => <div className="asset-identity"><span><ShieldCheck size={17} /></span><div><strong>{item.asset_name}</strong><small>{item.asset_id} · {item.cve_id}</small></div></div> }, { title: '软件包', dataIndex: 'package_name' }, { title: '当前版本', dataIndex: 'current_version' }, { title: '目标版本', render: (_, item) => <div className="stacked-cell"><strong>{item.target_version}</strong>{versionIssue(item) ? <small className="danger-text">{versionIssue(item)}</small> : null}</div> }, { title: '回滚预案', dataIndex: 'rollback_plan', width: 300, render: (value: string) => <span className="stacked-cell">{value}</span> }, { title: '级别', dataIndex: 'severity', render: (value: string) => <Tag color={vulnerabilitySeverityColors[value as VulnerabilitySeverity] ?? 'default'}>{vulnerabilitySeverityLabels[value as VulnerabilitySeverity] ?? '未知'}</Tag> }, { title: '来源', dataIndex: 'source_name' }, { title: '历史扫描', dataIndex: 'history_count', render: (value: number) => `${value} 次` }, { title: '状态', render: (_, item) => <Tag color={displayedStatus(item) === 'invalid_target' ? 'red' : item.status === 'pending_verification' ? 'blue' : item.status === 'fixed' ? 'green' : 'gold'}>{patchStatusLabels[displayedStatus(item)]}</Tag> }, { title: '执行', render: () => <Tag color="default">关闭</Tag> }, { title: '人工确认', render: (_, item) => <Button size="small" disabled={!canApproveAdvice(item) || approving} onClick={() => openApproval(item)}>确认建议</Button> }]} /></div>
    <Modal title="人工确认补丁建议" open={target !== null} onCancel={closeApproval} onOk={() => void approve()}
      okText="确认并等待验证" cancelText="取消" confirmLoading={approving} okButtonProps={{ disabled: approving || !valid }}
      cancelButtonProps={{ disabled: approving }} closable={!approving} maskClosable={!approving} keyboard={!approving} destroyOnHidden>
      {target ? <>
        <p>仅记录人工确认，不会安装或执行补丁，操作人使用当前登录账号。</p>
        <p>确认对象：<strong>{target.asset_name}</strong> · <span>{target.cve_id}</span> · <span>{target.package_name}</span></p>
        <p>版本：{target.current_version} → {target.target_version}</p>
        {target.target_version_issue ? <Alert type="error" showIcon message="补丁建议已阻断" description={target.target_version_issue} /> : null}
        <p className="confirmation-prompt">请输入确认文本：<strong>{approvalPhrase}</strong></p>
        <Input aria-label="确认文本" value={confirmation} disabled={approving} autoComplete="off"
          status={confirmationError ? 'error' : undefined} aria-invalid={Boolean(confirmationError)}
          aria-describedby={confirmationError ? fieldId + '-confirmation-error' : undefined}
          onChange={(event) => setConfirmation(event.target.value)} placeholder={`请输入“${approvalPhrase}”`} />
        {confirmationError ? <p id={fieldId + '-confirmation-error'} role="alert">{confirmationError}</p> : null}
        <Input.TextArea aria-label="审批原因" value={reason} disabled={approving} maxLength={1000}
          status={reasonError ? 'error' : undefined} aria-invalid={Boolean(reasonError)}
          aria-describedby={`${fieldId}-reason-hint${reasonError ? ` ${fieldId}-reason-error` : ''}`}
          onChange={(event) => setReason(event.target.value)} rows={2} style={{ marginTop: 12 }} />
        <p id={fieldId + '-reason-hint'}>审批原因需 2 至 1000 个字符，首尾空格不计入。</p>
        {reasonError ? <p id={fieldId + '-reason-error'} role="alert">{reasonError}</p> : null}
        {target.approval_phrase !== `APPROVE ${target.id}` ? <p role="alert">确认信息无效，请关闭后刷新补丁建议。</p> : null}
      </> : null}
    </Modal>
  </section>;
}
