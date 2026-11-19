import { useEffect, useId, useRef, useState } from 'react';
import { Alert, Button, Input, Pagination, Tag, Tooltip } from 'antd';
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { kubernetesRequestError } from '../api/kubernetes';
import {
  decideHelmApproval, executeHelmApproval, getHelmApprovals, runHelmAdmissionRehearsal, submitHelmApproval,
  type HelmAdmissionResult, type HelmApproval, type HelmApprovalList, type HelmExecutionResult,
  type HelmPreview, type HelmResource,
} from '../api/kubernetesChanges';
import { formatPlatformDateTime } from '../utils/platformPreferences';

const PAGE_SIZE = 10;
const statuses: Record<HelmApproval['status'], { text: string; color: string }> = {
  pending: { text: '待审批', color: 'warning' },
  approved: { text: '已通过 · 未执行', color: 'success' },
  rejected: { text: '已拒绝', color: 'error' },
  expired: { text: '已过期', color: 'default' },
  invalidated: { text: '已失效', color: 'default' },
};
const admissionErrors = new Map([
  ['identity_rejected', '测试身份校验未通过'],
  ['upstream_unauthorized', '测试身份认证未通过'],
  ['upstream_forbidden', '测试身份缺少所需权限'],
  ['admission_conflict', '测试命名空间已有同名资源'],
  ['admission_invalid', '测试集群拒绝了资源配置'],
  ['timeout', '测试集群响应超时'],
  ['network_error', '无法连接测试集群'],
  ['tls_chain', '测试集群证书不受信任'],
  ['tls_hostname', '测试集群证书与地址不匹配'],
  ['tls_expired', '测试集群证书已过期'],
  ['tls_error', '测试集群 TLS 连接失败'],
]);

export function HelmResources({ items }: { items: HelmResource[] }) {
  return <ul className="k8s-helm-resource-list">{items.map((item) =>
    <li key={`${item.api_version}:${item.kind}:${item.namespace}:${item.name}`}>
      <Tag>{item.kind}</Tag><span>{item.namespace}/{item.name}</span><small>{item.api_version}</small>
    </li>)}</ul>;
}

function PreviewApplication({ preview, busy, onSubmit }: {
  preview: HelmPreview; busy: boolean; onSubmit: (reason: string) => void;
}) {
  const [reason, setReason] = useState('');
  const id = useId();
  return <form className="k8s-helm-approval-form" onSubmit={(event) => {
    event.preventDefault();
    if (!busy && reason.trim().length >= 2) onSubmit(reason.trim());
  }}>
    <label htmlFor={id}>申请原因</label>
    <Input.TextArea id={id} rows={2} maxLength={240} value={reason} disabled={busy}
      onChange={(event) => setReason(event.target.value)} />
    <div className="k8s-helm-approval-actions">
      <Button type="primary" htmlType="submit" disabled={reason.trim().length < 2} loading={busy}>提交审批</Button>
      <span>预览有效至 {formatPlatformDateTime(preview.preview_expires_at)}</span>
    </div>
  </form>;
}

function DecisionForm({ busy, onConfirm }: { busy: boolean; onConfirm: (value: 'yes' | 'no') => void }) {
  const [confirmation, setConfirmation] = useState('');
  const id = useId();
  const value = confirmation.trim();
  const valid = value === 'yes' || value === 'no';
  return <form className="k8s-helm-approval-form" onSubmit={(event) => {
    event.preventDefault();
    if (!busy && valid) onConfirm(value);
  }}>
    <label htmlFor={id}>确认意见</label>
    <div className="k8s-helm-approval-actions">
      <Input id={id} placeholder="yes / no" autoComplete="off" maxLength={16} disabled={busy}
        value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
      <Button type="primary" htmlType="submit" autoInsertSpace={false} disabled={!valid} loading={busy}>确认</Button>
    </div>
  </form>;
}

function AdmissionResult({ result }: { result: HelmAdmissionResult }) {
  const passed = result.status === 'passed';
  const error = result.error_code && admissionErrors.get(result.error_code);
  return <Alert type={passed ? 'success' : 'error'} showIcon
    message={passed ? '准入演练通过 · 未部署' : '准入演练失败 · 未部署'}
    description={<div>
      {!passed && <div>{error || '准入演练未通过'}</div>}
      <div>通过检查 {result.checked} / {result.total} · 审计编号 {result.trace_id}</div>
    </div>} />;
}

function ExecutionResult({ result }: { result: HelmExecutionResult }) {
  const succeeded = result.status === 'succeeded';
  const rolledBack = result.status === 'rolled_back';
  return <Alert type={succeeded ? 'success' : rolledBack ? 'warning' : 'error'} showIcon
    message={succeeded ? '隔离执行完成' : rolledBack ? '执行失败，已回滚' :
      result.status === 'failed' ? '执行失败，未创建资源' : '执行失败，回滚未完成'}
    description={<div>
      <div>{succeeded ? `已创建 ${result.created} 个资源并验证 ${result.validated} 个资源` : '未保留本次执行产生的资源'}</div>
      <div>审计编号 {result.trace_id}</div>
    </div>} />;
}

function AdmissionRehearsal({ clusterId, approvalId, disabled }: {
  clusterId: string; approvalId: string; disabled: boolean;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<HelmAdmissionResult>();
  const [requestFailed, setRequestFailed] = useState(false);
  const active = useRef<AbortController>();
  const id = useId();
  const valid = confirmation.trim() === 'yes';
  useEffect(() => () => active.current?.abort(), []);

  async function run() {
    if (!valid || disabled || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setResult(undefined);
    setRequestFailed(false);
    try {
      const response = await runHelmAdmissionRehearsal(clusterId, approvalId, controller.signal);
      if (!controller.signal.aborted) {
        setConfirmation('');
        setResult(response);
      }
    } catch {
      if (!controller.signal.aborted) setRequestFailed(true);
    } finally {
      active.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section aria-label="准入演练">
    <form className="k8s-helm-approval-form" aria-busy={busy} onSubmit={(event) => {
      event.preventDefault();
      void run();
    }}>
      <label htmlFor={id}>确认演练</label>
      <div className="k8s-helm-approval-actions">
        <Input id={id} placeholder="yes" autoComplete="off" maxLength={16}
          disabled={disabled || busy} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <Button type="primary" htmlType="submit" autoInsertSpace={false}
          disabled={disabled || busy || !valid} loading={busy}>运行准入演练</Button>
      </div>
    </form>
    {result && <AdmissionResult result={result} />}
    {requestFailed && <Alert type="error" showIcon message="准入演练请求失败，请刷新审批记录后重试" />}
  </section>;
}

function HelmExecution({ clusterId, approvalId, disabled }: {
  clusterId: string; approvalId: string; disabled: boolean;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<HelmExecutionResult>();
  const [requestFailed, setRequestFailed] = useState(false);
  const active = useRef<AbortController>();
  const id = useId();
  const valid = confirmation.trim() === 'yes';
  useEffect(() => () => active.current?.abort(), []);

  async function run() {
    if (!valid || disabled || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setResult(undefined);
    setRequestFailed(false);
    try {
      const response = await executeHelmApproval(clusterId, approvalId, controller.signal);
      if (!controller.signal.aborted) {
        setConfirmation('');
        setResult(response);
      }
    } catch {
      if (!controller.signal.aborted) setRequestFailed(true);
    } finally {
      active.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section aria-label="隔离执行">
    <Alert type="warning" showIcon message="仅隔离环境执行"
      description="仅允许已批准的 ConfigMap 变更，失败时只回滚本次新建资源。" />
    <form className="k8s-helm-approval-form" aria-busy={busy} onSubmit={(event) => {
      event.preventDefault();
      void run();
    }}>
      <label htmlFor={id}>确认执行</label>
      <div className="k8s-helm-approval-actions">
        <Input id={id} placeholder="yes" autoComplete="off" maxLength={16}
          disabled={disabled || busy} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        <Button type="primary" htmlType="submit" autoInsertSpace={false}
          disabled={disabled || busy || !valid} loading={busy}>执行</Button>
      </div>
    </form>
    {result && <ExecutionResult result={result} />}
    {requestFailed && <Alert type="error" showIcon message="隔离执行请求失败，请刷新审批记录后重试" />}
  </section>;
}

function ApprovalDetails({ clusterId, item, busy, onConfirm }: {
  clusterId: string; item: HelmApproval; busy: boolean; onConfirm: (value: 'yes' | 'no') => void;
}) {
  return <section className="k8s-helm-approval-detail" aria-label="审批详情">
    <dl>
      <div><dt>Chart</dt><dd>{item.chart.name} · {item.chart.version}</dd></div>
      <div><dt>目标命名空间</dt><dd>{item.namespace}</dd></div>
      <div><dt>集群修订</dt><dd>{item.cluster_revision}</dd></div>
      <div><dt>申请人</dt><dd>{item.requested_by}</dd></div>
      <div><dt>申请时间</dt><dd>{formatPlatformDateTime(item.submitted_at)}</dd></div>
      <div><dt>审批有效至</dt><dd>{formatPlatformDateTime(item.expires_at)}</dd></div>
      {item.reviewed_by && <div><dt>审批人</dt><dd>{item.reviewed_by}</dd></div>}
      {item.reviewed_at && <div><dt>审批时间</dt><dd>{formatPlatformDateTime(item.reviewed_at)}</dd></div>}
      <div className="k8s-helm-wide"><dt>Chart 摘要</dt><dd><code>{item.chart.digest}</code></dd></div>
      <div className="k8s-helm-wide"><dt>申请原因</dt><dd>{item.reason ?? '—'}</dd></div>
    </dl>
    <HelmResources items={item.resources} />
    {item.can_review && item.status === 'pending' && <DecisionForm busy={busy} onConfirm={onConfirm} />}
    {item.status === 'approved' && item.can_rehearse === true &&
      <AdmissionRehearsal key={`${clusterId}:${item.id}`} clusterId={clusterId} approvalId={item.id} disabled={busy} />}
    {item.status === 'approved' && item.can_execute === true &&
      <HelmExecution key={`${clusterId}:${item.id}:execution`} clusterId={clusterId} approvalId={item.id} disabled={busy} />}
  </section>;
}

export function KubernetesHelmApprovals({ clusterId, preview }: { clusterId: string; preview?: HelmPreview }) {
  const [data, setData] = useState<HelmApprovalList>();
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [selection, setSelection] = useState<string>();
  const [submittedPreview, setSubmittedPreview] = useState<string>();
  const [listError, setListError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const mutation = useRef<AbortController>();
  const previewId = preview?.preflight.status !== 'blocked' ? preview?.preview_id : null;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setListError('');
    getHelmApprovals(clusterId, (page - 1) * PAGE_SIZE, controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setListError(kubernetesRequestError(reason));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [clusterId, page, reload]);
  useEffect(() => () => mutation.current?.abort(), []);

  async function change(run: (signal: AbortSignal) => Promise<HelmApproval>, submitting?: string) {
    if (mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setError('');
    setBusy(true);
    try {
      const item = await run(controller.signal);
      if (controller.signal.aborted) return;
      if (submitting) {
        setSubmittedPreview(submitting);
        setPage(1);
      } else {
        setData((current) => current && { ...current, items: current.items.map((entry) => entry.id === item.id ? item : entry) });
      }
      setReload((value) => value + 1);
    } catch (reason) {
      if (!controller.signal.aborted) setError(kubernetesRequestError(reason));
    } finally {
      mutation.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <section className="k8s-helm-approvals" aria-label="Helm 审批">
    {preview && previewId && previewId !== submittedPreview &&
      <PreviewApplication key={previewId} preview={preview} busy={busy}
        onSubmit={(reason) => { void change((signal) => submitHelmApproval(clusterId, previewId, reason, signal), previewId); }} />}
    {error && <Alert type="error" showIcon message={error} />}
    <div className="k8s-helm-approval-heading"><h3>审批记录</h3>
      <Tooltip title="刷新审批记录">
        <Button size="small" aria-label="刷新审批记录" disabled={busy || loading}
          icon={<RefreshCw size={14} />} onClick={() => setReload((value) => value + 1)} />
      </Tooltip>
    </div>
    <div aria-busy={loading}>
      {listError && <Alert type="error" showIcon message={listError} />}
      {loading && !data && <span role="status">正在读取审批记录…</span>}
      {!loading && !listError && data?.total === 0 && <span>暂无审批记录</span>}
      {data?.items.map((item) => <div className="k8s-helm-approval-row" key={item.id}>
        <Button type="text" aria-label={`查看审批：${item.release_name}`} aria-expanded={selection === item.id}
          className="k8s-helm-approval-toggle" disabled={busy}
          onClick={() => { setError(''); setSelection((current) => current === item.id ? undefined : item.id); }}>
          {selection === item.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          <span>{item.release_name} · {item.namespace}</span>
          <Tag color={statuses[item.status].color}>{statuses[item.status].text}</Tag>
        </Button>
        {selection === item.id && <ApprovalDetails clusterId={clusterId} item={item} busy={busy || loading || !!listError}
          onConfirm={(value) => { void change((signal) => decideHelmApproval(clusterId, item.id, value, signal)); }} />}
      </div>)}
      {data && data.total > PAGE_SIZE && <Pagination simple current={page} pageSize={PAGE_SIZE} total={data.total}
        disabled={loading || busy} onChange={(value) => { setSelection(undefined); setPage(value); }} />}
    </div>
  </section>;
}
