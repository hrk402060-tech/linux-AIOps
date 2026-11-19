import { useEffect, useId, useRef, useState } from 'react';
import { Alert, Button, Input, Pagination, Tag, Tooltip } from 'antd';
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { kubernetesRequestError } from '../api/kubernetes';
import {
  decideHelmApproval, getHelmApprovals, submitHelmApproval,
  type HelmApproval, type HelmApprovalList, type HelmPreview, type HelmResource,
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

function ApprovalDetails({ item, busy, onConfirm }: {
  item: HelmApproval; busy: boolean; onConfirm: (value: 'yes' | 'no') => void;
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
        {selection === item.id && <ApprovalDetails item={item} busy={busy || loading || !!listError}
          onConfirm={(value) => { void change((signal) => decideHelmApproval(clusterId, item.id, value, signal)); }} />}
      </div>)}
      {data && data.total > PAGE_SIZE && <Pagination simple current={page} pageSize={PAGE_SIZE} total={data.total}
        disabled={loading || busy} onChange={(value) => { setSelection(undefined); setPage(value); }} />}
    </div>
  </section>;
}
