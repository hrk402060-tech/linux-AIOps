import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Empty, Pagination, Select, Spin, Tag, Tooltip, message } from 'antd';
import {
  BellRing, Bot, CheckCircle2, CircleAlert, History, RefreshCw, Sparkles,
  XCircle,
} from 'lucide-react';

import {
  createAgentTriage, getAgentWorkspace, reviewAgentRecommendation,
} from '../api/ai-agent';
import type { AuthUser } from '../api/auth';
import type { AgentFinding, AgentRecommendation, AgentWorkspace } from '../types/ai-agent';
import { vulnerabilitySeverityColors, vulnerabilitySeverityLabels } from '../types/vulnerability';

const emptyWorkspace: AgentWorkspace = {
  providers: [],
  summary: { pending_findings: 0, pending_review: 0, approved: 0, urgent_alerts: 0 },
  findings: [], recommendations: [],
  queue_total: 0, queue_page: 1, history_total: 0, history_page: 1,
};
const priorityText: Record<string, string> = {
  urgent: '紧急', high: '高优先级', normal: '常规', review: '先核验',
};

export function AIAgentPage({ currentUser }: { currentUser: AuthUser }) {
  const canReview = currentUser.role !== 'auditor';
  const [workspace, setWorkspace] = useState(emptyWorkspace);
  const [selection, setSelection] = useState<{ findingId: string | null; adviceId: string | null }>({ findingId: null, adviceId: null });
  const refreshSequence = useRef(0);
  const mutationActive = useRef(false);
  const mounted = useRef(true);
  const [providerId, setProviderId] = useState('local-rules');
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [workspaceStale, setWorkspaceStale] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const load = async (queuePage = workspace.queue_page, historyPage = workspace.history_page, preferredAdviceId?: string) => {
    const sequence = ++refreshSequence.current;
    setLoading(true);
    try {
      const data = await getAgentWorkspace({ queuePage, historyPage });
      if (!mounted.current || sequence !== refreshSequence.current) return;
      setWorkspace(data);
      setWorkspaceStale(false);
      setProviderId((current) => data.providers.some((item) => item.id === current) ? current : data.providers[0]?.id ?? 'local-rules');
      setSelection((current) => {
        const findingId = data.findings.find((item) => item.id === current.findingId)?.id ?? data.findings[0]?.id ?? null;
        const advice = data.recommendations.find((item) => item.id === (preferredAdviceId ?? current.adviceId))
          ?? data.recommendations.find((item) => item.finding_id === findingId) ?? data.recommendations[0];
        return { findingId: advice ? data.findings.find((item) => item.id === advice.finding_id)?.id ?? null : findingId, adviceId: advice?.id ?? null };
      });
    } catch (error) {
      if (mounted.current && sequence === refreshSequence.current) {
        setWorkspaceStale(true);
        messageApi.error(error instanceof Error ? error.message : 'AI Agent 数据加载失败');
      }
    } finally { if (mounted.current && sequence === refreshSequence.current) setLoading(false); }
  };

  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; refreshSequence.current += 1; }; }, []);
  const finding = useMemo(
    () => workspace.findings.find((item) => item.id === selection.findingId) ?? null,
    [selection.findingId, workspace.findings],
  );
  const selectedAdvice = workspace.recommendations.find((item) => item.id === selection.adviceId) ?? null;
  const selectedProvider = workspace.providers.find((item) => item.id === providerId);
  const selectFinding = (item: AgentFinding) => {
    setSelection({ findingId: item.id, adviceId: workspace.recommendations.find((advice) => advice.finding_id === item.id)?.id ?? null });
  };
  const selectAdvice = (id: string) => {
    const advice = workspace.recommendations.find((item) => item.id === id);
    setSelection({ adviceId: advice?.id ?? null, findingId: workspace.findings.find((item) => item.id === advice?.finding_id)?.id ?? null });
  };

  const mutateAdvice = async (request: () => Promise<AgentRecommendation>, success: string, resetHistory: boolean) => {
    if (!canReview || workspaceStale || mutationActive.current) return;
    mutationActive.current = true; refreshSequence.current += 1; setWorking(true); setLoading(false);
    try {
      const advice = await request();
      if (!mounted.current) return;
      await load(workspace.queue_page, resetHistory ? 1 : workspace.history_page, advice.id);
      messageApi.success(success);
    } catch (error) {
      if (!mounted.current) return;
      messageApi.error(error instanceof Error ? error.message : '建议操作失败');
      await load();
    } finally { mutationActive.current = false; if (mounted.current) setWorking(false); }
  };

  const triage = () => {
    if (finding) void mutateAdvice(() => createAgentTriage(finding.id, providerId), '研判建议已生成，等待人工确认', true);
  };
  const review = (decision: 'approved' | 'rejected') => {
    if (selectedAdvice?.status === 'pending_review') void mutateAdvice(() => reviewAgentRecommendation(selectedAdvice.id, decision), decision === 'approved' ? '建议已人工确认，未执行修复' : '建议已驳回', false);
  };

  return <section className="ai-agent-page">{contextHolder}
    <div className="page-heading agent-heading">
      <div><p className="eyebrow">本地安全智能体</p><h1>AI Agent</h1></div>
      <Tooltip title="刷新"><Button aria-label="刷新" aria-busy={loading} icon={<RefreshCw size={17} />} disabled={working} onClick={() => void load()} /></Tooltip>
    </div>

    <div className="metric-band agent-metrics">
      <div><span>待研判漏洞</span><strong>{workspace.summary.pending_findings}</strong></div>
      <div><span>待人工审核</span><strong>{workspace.summary.pending_review}</strong></div>
      <div><span>已确认建议</span><strong>{workspace.summary.approved}</strong></div>
      <div><span>值守告警</span><strong className="metric-text">{workspace.summary.urgent_alerts}</strong></div>
    </div>

    <Spin spinning={loading}>
      <div className="agent-provider-row">
        <div><Bot size={18} /><span><strong>推理引擎</strong><small>已预留多厂商 Provider 扩展接口</small></span></div>
        <Select aria-label="推理引擎" value={providerId} disabled={working} onChange={setProviderId} options={workspace.providers.map((item) => ({ value: item.id, label: item.name }))} />
        <Tag color={selectedProvider?.kind === 'local' ? 'green' : 'default'}>{selectedProvider?.kind === 'local' ? '本地就绪' : '外部推理未接通'}</Tag>
      </div>

      <div className="agent-workbench">
        <div className="agent-queue">
          <div className="agent-section-title"><span><BellRing size={17} />研判队列</span><small>本页 {workspace.findings.length} / 共 {workspace.queue_total} 项</small></div>
          {workspace.findings.length ? workspace.findings.map((item) => (
            <FindingItem key={item.id} item={item} selected={item.id === selection.findingId} disabled={working || loading} onSelect={() => selectFinding(item)} />
          )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待研判漏洞" />}
          <nav aria-label="研判队列分页"><Pagination size="small" current={workspace.queue_page} total={workspace.queue_total} pageSize={50} showSizeChanger={false} disabled={working || loading} onChange={(page) => void load(page, workspace.history_page)} /></nav>
        </div>

        <div className="agent-analysis">
          <div className="agent-section-title"><span><History size={17} />全局建议历史</span><small>本页 {workspace.recommendations.length} / 共 {workspace.history_total} 条</small></div>
          <Select aria-label="全局建议历史" style={{ width: '100%', marginBottom: 12 }} placeholder="暂无建议历史" value={selection.adviceId ?? undefined} disabled={working || loading} onChange={selectAdvice} options={workspace.recommendations.map((item) => ({ value: item.id, label: `${item.cve_id} · ${item.asset_name} · ${statusLabel(item.status)} · ${item.id}` }))} />
          <nav aria-label="建议历史分页"><Pagination size="small" current={workspace.history_page} total={workspace.history_total} pageSize={50} showSizeChanger={false} disabled={working || loading} onChange={(page) => void load(workspace.queue_page, page)} /></nav>
          <div className="agent-section-title"><span><Sparkles size={17} />研判与修复建议</span>{selectedAdvice ? <Tag color={selectedAdvice.status === 'pending_review' ? 'gold' : selectedAdvice.status === 'approved' ? 'green' : 'default'}>{statusLabel(selectedAdvice.status)}</Tag> : null}</div>
          {selectedAdvice ? <RecommendationView advice={selectedAdvice} /> : finding ? <div className="agent-empty-analysis"><Bot size={28} /><strong>{finding.cve_id}</strong><span>基于当前证据链生成本地研判与修复建议</span></div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择漏洞" />}
          <div className="agent-actions">
            {canReview ? <Button type="primary" icon={<Sparkles size={16} />} disabled={workspaceStale || loading || working || !finding || selectedProvider?.status !== 'ready'} loading={working} onClick={triage}>生成研判建议</Button> : null}
            {canReview && selectedAdvice?.status === 'pending_review' ? <>
              <Button icon={<CheckCircle2 size={16} />} disabled={workspaceStale || working || loading} onClick={() => review('approved')}>人工确认建议</Button>
              <Button danger icon={<XCircle size={16} />} disabled={workspaceStale || working || loading} onClick={() => review('rejected')}>驳回</Button>
            </> : null}
          </div>
        </div>
      </div>
    </Spin>
  </section>;
}

function FindingItem({ item, selected, disabled, onSelect }: { item: AgentFinding; selected: boolean; disabled: boolean; onSelect: () => void }) {
  return <button type="button" className={'agent-finding ' + (selected ? 'selected' : '')} disabled={disabled} onClick={onSelect}>
    <span><strong>{item.cve_id}</strong><Tag color={vulnerabilitySeverityColors[item.severity]}>{vulnerabilitySeverityLabels[item.severity]}</Tag></span>
    <b>{item.title}</b><small>{item.asset_name} · {item.package_name}</small>
    <em>{item.conclusion === 'confirmed' ? '厂商公告已确认' : '需人工核验 backport'}</em>
  </button>;
}

function RecommendationView({ advice }: { advice: AgentRecommendation }) {
  return <div className="recommendation-view">
    <div className="recommendation-summary"><Tag color={advice.priority === 'urgent' ? 'red' : 'gold'}>{priorityText[advice.priority]}</Tag><strong>{advice.cve_id} · {advice.asset_name}</strong><p>{advice.risk_analysis}</p></div>
    {advice.status !== 'pending_review' ? <div className="agent-section-title"><span>审核人：<strong>{advice.reviewed_by ?? '历史记录未记载'}</strong></span><time>{advice.reviewed_at ? new Date(advice.reviewed_at).toLocaleString('zh-CN') : '审核时间未记载'}</time></div> : null}
    <div className="recommendation-columns">
      <section><h3>修复建议</h3><ol>{advice.remediation_steps.map((step) => <li key={step}>{step}</li>)}</ol></section>
      <section><h3>验证步骤</h3><ol>{advice.validation_steps.map((step) => <li key={step}>{step}</li>)}</ol></section>
    </div>
    <div className="rollback-note"><strong>回滚要求</strong><span>{advice.rollback_plan}</span></div>
    <div className="agent-boundary-note"><CircleAlert size={15} /><span>此处仅记录建议与人工审核结果，不会向资产下发命令。</span></div>
  </div>;
}

function statusLabel(status: AgentRecommendation['status']): string {
  return { pending_review: '待人工审核', approved: '已人工确认', rejected: '已驳回' }[status];
}
