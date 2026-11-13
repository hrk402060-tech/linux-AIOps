import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Descriptions, Drawer, Empty, Input, List, Modal, Select,
  Space, Table, Tag, Timeline, Typography, message,
} from 'antd';
import { Bell, Brain, Eye, KeyRound, RefreshCw, StepForward, TerminalSquare, TicketCheck } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import { getAssets } from '../../api/assets';
import {
  createIncidentFromAlert, updateExternalAlertStatus,
  type AlertSeverity, type AlertStatus, type DiagnosticJob,
  type AnalysisStatus, type ExternalAlert,
} from '../../api/externalAlerts';
import type { Asset } from '../../types/asset';
import { AlertNotificationHistory } from './AlertNotificationHistory';
import './AlertNotifications.css';
import { AlertWorkflow } from './AlertWorkflow';
import { RecoveryVerificationPanel } from './RecoveryVerificationPanel';
import { DiagnosticJobActionModal, type DiagnosticJobAction } from './DiagnosticJobActionModal';
import { DiagnosticJobHistoryPanel } from './DiagnosticJobHistoryPanel';
import { DiagnosticJobTag, isActiveDiagnosticJob } from './DiagnosticJobTag';
import { useExternalAlertData, type AlertDetail } from './useExternalAlertData';

const PAGE_SIZE = 20;

const severityLabels: Record<AlertSeverity, string> = {
  critical: '严重', high: '高危', medium: '中危', low: '低危',
};
const severityColors: Record<AlertSeverity, string> = {
  critical: 'red', high: 'volcano', medium: 'gold', low: 'blue',
};
const statusLabels: Record<AlertStatus, string> = {
  open: '待确认', acknowledged: '已确认', in_progress: '处理中', resolved: '人工登记完成', closed: '人工已关闭',
};
const analysisLabels: Record<AnalysisStatus, string> = {
  running: '研判中', completed: '已完成', partial: '部分采集失败', failed: '研判失败', cancelled: '已取消',
};
const nextStatus: Partial<Record<AlertStatus, AlertStatus>> = {
  open: 'acknowledged', acknowledged: 'in_progress', in_progress: 'resolved', resolved: 'closed',
};
const timelineEventLabels: Record<string, string> = {
  ingested: '告警接入', duplicate: '重复接收', reopened: '告警复发',
  status: '状态变更', diagnostic_queued: '诊断入队', diagnostic_finished: '诊断完成',
  incident_created: '转为故障', recovery_started: '开始恢复验证',
  recovery_observing: '恢复验证观察中', recovery_verified: '恢复检查通过',
  recovery_failed: '恢复检查失败', recovery_blocked: '恢复验证受阻',
  recovery_evaluated: '恢复验证', recovery_cancelled: '取消恢复验证',
};
const timelineStatusLabels: Record<string, string> = {
  ...statusLabels, queued: '排队中', running: '执行中', completed: '已完成',
  partial: '部分完成', failed: '失败', cancelled: '已取消',
  observing: '待观察', verified: '检查通过', blocked: '验证受阻',
};
const options = (values: Record<string, string>) =>
  Object.entries(values).map(([value, label]) => ({ value, label }));

export function ExternalAlertsTab({ currentUser, initialAssetId, initialAlertId, onOpenTerminal, onOpenIncidents, onOpenConfiguration }: {
  currentUser: Pick<AuthUser, 'role'>;
  initialAssetId?: string;
  initialAlertId?: string;
  onOpenTerminal?: (assetId: string) => void;
  onOpenIncidents?: () => void;
  onOpenConfiguration?: (tab: 'notifications' | 'diagnostic-grants', alert: ExternalAlert) => void;
}) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [serverFilters, setServerFilters] = useState<Record<string, string>>((): Record<string, string> => initialAssetId ? { asset_id: initialAssetId } : {});
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [operationLoading, setOperationLoading] = useState(false);
  const [diagnosticTarget, setDiagnosticTarget] = useState<DiagnosticJobAction | null>(null);
  const [advanceTarget, setAdvanceTarget] = useState<ExternalAlert | null>(null);
  const [incidentTarget, setIncidentTarget] = useState<ExternalAlert | null>(null);
  const [note, setNote] = useState('');
  const operationPending = useRef(false);
  const [messages, holder] = message.useMessage();
  const readOnly = currentUser.role === 'auditor';
  const {
    data, detail, loading: refreshing, authoritative, detailLoading, detailAuthoritative,
    load, refresh, showDetail, closeDetail,
  }
    = useExternalAlertData(serverFilters, page, (text) => messages.error(text));
  const loading = refreshing || operationLoading;
  useEffect(() => {
    closeDetail();
    if (initialAlertId) void showDetail({ id: initialAlertId, asset_id: initialAssetId });
  }, [initialAlertId, initialAssetId, showDetail, closeDetail]);
  useEffect(() => {
    let stopped = false;
    void getAssets('active').then((items) => { if (!stopped) setAssets(items); }).catch(() => { if (!stopped) messages.error('资产列表加载失败'); });
    return () => { stopped = true; };
  }, []);
  const metrics = useMemo(() => data.summary ?? {
    open: data.items.filter((item) => !['resolved', 'closed'].includes(item.status)).length,
    urgent: data.items.filter((item) => ['critical', 'high'].includes(item.severity)).length,
    pending_rca: data.items.filter((item) => item.latest_job ? item.latest_job.status !== 'completed' : item.latest_analysis?.status !== 'completed').length,
    linked: data.items.filter((item) => item.incident_id).length,
  }, [data.items, data.summary]);
  const setServerFilter = (key: string, value?: string) => setServerFilters((current) => {
    const next = { ...current };
    if (value) next[key] = value; else delete next[key];
    setPage(1);
    return next;
  });
  const advance = async () => {
    if (!advanceTarget || readOnly || !authoritative || operationPending.current || note.trim().length < 3) return;
    const targetStatus = nextStatus[advanceTarget.status];
    if (!targetStatus) return;
    operationPending.current = true; setOperationLoading(true);
    try {
      await updateExternalAlertStatus(advanceTarget.id, targetStatus, note.trim());
      messages.success(`告警已推进至${statusLabels[targetStatus]}`);
      setAdvanceTarget(null); setNote(''); await refresh();
    } catch (error) { messages.error(error instanceof Error ? error.message : '状态推进失败'); }
    finally { operationPending.current = false; setOperationLoading(false); }
  };
  const createIncident = async () => {
    if (!incidentTarget || readOnly || !authoritative || operationPending.current || note.trim().length < 3) return;
    operationPending.current = true; setOperationLoading(true);
    try {
      await createIncidentFromAlert(incidentTarget.id, note.trim());
      messages.success('已人工转为故障并保留关联证据');
      setIncidentTarget(null); setNote(''); await refresh();
    } catch (error) { messages.error(error instanceof Error ? error.message : '转故障失败'); }
    finally { operationPending.current = false; setOperationLoading(false); }
  };
  return <div>{holder}
    <div className="metric-band alert-metrics"><div><span>筛选后待处理</span><strong>{metrics.open}</strong></div><div><span>筛选后严重 / 高危</span><strong>{metrics.urgent}</strong></div><div><span>筛选后待完成研判</span><strong>{metrics.pending_rca}</strong></div><div><span>筛选后已关联故障</span><strong>{metrics.linked}</strong></div></div>
    <div className="alert-filter-row">
      <Input.Search allowClear aria-label="搜索外部告警" value={searchInput} placeholder="标题、连接器、外部编号或资产" onChange={(event) => { setSearchInput(event.target.value); if (!event.target.value) setServerFilter('q'); }} onSearch={(value) => setServerFilter('q', value.trim() || undefined)} />
      <Select allowClear aria-label="外部告警级别" placeholder="严重度" options={options(severityLabels)} onChange={(value) => setServerFilter('severity', value)} />
      <Select allowClear aria-label="外部告警状态" placeholder="处置状态" options={options(statusLabels)} onChange={(value) => setServerFilter('status', value)} />
      <Select allowClear aria-label="根因分析状态" placeholder="研判状态" options={options({ ...analysisLabels, unassessed: '未研判' })} onChange={(value) => setServerFilter('analysis_status', value)} />
      <Select allowClear showSearch optionFilterProp="label" aria-label="外部告警资产" placeholder="服务器资产" value={serverFilters.asset_id} options={assets.map((item) => ({ value: item.id, label: `${item.name} · ${item.host}` }))} onChange={(value) => setServerFilter('asset_id', value)} />
      <Button aria-label="刷新外部告警" icon={<RefreshCw size={16} />} onClick={() => void load()} />
    </div>
    <div className="table-frame"><Table<ExternalAlert> rowKey="id" loading={loading} dataSource={data.items} pagination={{ current: page, pageSize: PAGE_SIZE, total: data.total, showSizeChanger: false, onChange: setPage }} scroll={{ x: 1220 }} locale={{ emptyText: <Empty description="暂无符合条件的告警" /> }} columns={[
      { title: '告警', width: 250, render: (_, item) => <div className="asset-identity"><div><strong>{item.title}</strong><small>{item.connector_name ?? `连接器 ${item.connector_id}`} · 累计 {item.occurrence_count ?? 1} 次</small></div></div> },
      { title: '资产', dataIndex: 'asset_name', width: 150, render: (value: string, item) => value || item.asset_id },
      { title: '级别', dataIndex: 'severity', width: 90, render: (value: AlertSeverity) => <Tag color={severityColors[value]}>{severityLabels[value]}</Tag> },
      { title: '状态', dataIndex: 'status', width: 100, render: (value: AlertStatus) => <Tag>{statusLabels[value]}</Tag> },
      { title: '只读诊断', width: 130, render: (_, item) => item.latest_job ? <DiagnosticJobTag job={item.latest_job} /> : <AnalysisTag status={item.latest_analysis?.status} errorCode={item.latest_analysis?.error_code} /> },
      { title: '置信度', width: 100, render: () => <Typography.Text type="secondary">未提供</Typography.Text> },
      { title: '接收时间', dataIndex: 'received_at', width: 170, render: (value: string) => new Date(value).toLocaleString() },
      { title: '操作', fixed: 'right', width: readOnly ? 70 : 210, render: (_, item) => <Space size={4}><Button aria-label={`查看 ${item.title}`} icon={<Eye size={16} />} onClick={() => void showDetail(item)} />{!readOnly ? <><Button aria-label={`研判 ${item.title}`} disabled={loading || !authoritative || isActiveDiagnosticJob(item.latest_job)} icon={<Brain size={16} />} onClick={() => setDiagnosticTarget({ kind: 'enqueue', alert: item })} /><Button aria-label={`推进 ${item.title}`} disabled={!nextStatus[item.status]} icon={<StepForward size={16} />} onClick={() => { setAdvanceTarget(item); setNote(''); }} /><Button aria-label={`转为故障 ${item.title}`} disabled={Boolean(item.incident_id)} icon={<TicketCheck size={16} />} onClick={() => { setIncidentTarget(item); setNote(''); }} /></> : null}</Space> },
    ]} /></div>
    <AlertDetailDrawer detail={detail} currentUser={currentUser} onClose={closeDetail}
      onOpenConfiguration={onOpenConfiguration}
      onOpenIncidents={onOpenIncidents}
      actionsDisabled={loading || !authoritative || detailLoading || !detailAuthoritative}
      onOpenTerminal={onOpenTerminal}
      terminalAssetEnabled={Boolean(detail?.alert.asset_id && assets.some((item) =>
        item.id === detail.alert.asset_id && item.lifecycle_status === 'enabled'))}
      onAdvance={(item) => { setAdvanceTarget(item); setNote(''); }}
      onIncident={(item) => { setIncidentTarget(item); setNote(''); }}
      onUpdated={() => { void refresh(); }} onAnalysisPage={(nextPage) => { if (detail) void showDetail(detail.alert, nextPage); }}
      onCancelJob={(job) => { if (detail && !readOnly) setDiagnosticTarget({ kind: 'cancel', alert: detail.alert, job }); }} />
    <DiagnosticJobActionModal target={diagnosticTarget} onClose={() => setDiagnosticTarget(null)} onUpdated={refresh} />
    <Modal title="人工推进告警" open={advanceTarget !== null} onCancel={() => setAdvanceTarget(null)} onOk={() => void advance()} okText="确认推进" confirmLoading={loading} okButtonProps={{ disabled: note.trim().length < 3 }} destroyOnHidden><p>{advanceTarget ? `${statusLabels[advanceTarget.status]} → ${statusLabels[nextStatus[advanceTarget.status] ?? advanceTarget.status]}` : ''}</p><Input.TextArea aria-label="推进说明" value={note} onChange={(event) => setNote(event.target.value)} placeholder="请输入人工判断和处置说明（至少 3 个字符）" rows={3} /></Modal>
    <Modal title="人工转为故障" open={incidentTarget !== null} onCancel={() => setIncidentTarget(null)} onOk={() => void createIncident()} okText="确认转为故障" confirmLoading={loading} okButtonProps={{ disabled: note.trim().length < 3 }} destroyOnHidden><Input.TextArea aria-label="转故障说明" value={note} onChange={(event) => setNote(event.target.value)} placeholder="请输入影响判断和转故障原因" rows={3} style={{ marginTop: 12 }} /></Modal>
  </div>;
}

function AnalysisTag({ status, errorCode }: { status?: AnalysisStatus; errorCode?: string | null }) {
  if (errorCode === 'diagnostic_authorization_required') return <Tag color="gold">待授权</Tag>;
  if (!status) return <Tag>未研判</Tag>;
  return <Tag color={status === 'cancelled' ? undefined : status === 'completed' ? 'green' : status === 'failed' ? 'red' : status === 'partial' ? 'gold' : 'blue'}>{analysisLabels[status]}</Tag>;
}

function formatEvidence(value: unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function AlertDetailDrawer({ detail, currentUser, onClose, onUpdated, onCancelJob, onAnalysisPage, onAdvance, onIncident, actionsDisabled, onOpenTerminal, onOpenIncidents, onOpenConfiguration, terminalAssetEnabled }: {
  detail: AlertDetail | null; currentUser: Pick<AuthUser, 'role'>; onClose: () => void; onUpdated: () => void;
  onAdvance: (alert: ExternalAlert) => void; onIncident: (alert: ExternalAlert) => void;
  actionsDisabled: boolean;
  onOpenTerminal?: (assetId: string) => void; onOpenIncidents?: () => void; terminalAssetEnabled: boolean;
  onOpenConfiguration?: (tab: 'notifications' | 'diagnostic-grants', alert: ExternalAlert) => void;
  onCancelJob: (job: DiagnosticJob) => void;
  onAnalysisPage: (page: number) => void;
}) {
  const alert = detail?.alert;
  const analysis = alert?.latest_analysis;
  const limitation = analysis?.error_code === 'diagnostic_authorization_required'
    ? '专用只读身份待授权，未连接服务器、未执行命令。'
    : analysis?.status === 'partial' ? '只读诊断部分采集失败，证据不完整，需人工排查。'
    : analysis?.error_code ? `只读诊断失败（${analysis.error_code}），证据不完整，需人工排查。`
    : ['readonly-linux-2026.10.07', 'readonly-kubernetes-2026.10.15', 'readonly-docker-2026.10.15',
      'readonly-mysql-2026.10.22'].includes(analysis?.policy_version ?? '') ? '固定只读动作形成采集证据，不能独立证明根因或已恢复；处理仍需人工确认。'
    : '专用只读授权和服务连接器尚未配置；历史研判结果不代表已获得授权。';
  const evidence = Object.entries(analysis?.evidence ?? {});
  return <Drawer title={alert ? `${alert.title} · 研判详情` : '告警研判详情'} open={detail !== null} onClose={onClose} width="min(680px, 100vw)">
    {alert ? <div className="alert-detail">
      <AlertWorkflow alert={alert} recordCount={detail.timeline.length} />
      <Space wrap style={{ margin: '12px 0' }}>
        {currentUser.role === 'admin' && onOpenConfiguration ? <>
          <Button icon={<KeyRound size={15} />} disabled={actionsDisabled || !terminalAssetEnabled}
            onClick={() => onOpenConfiguration('diagnostic-grants', alert)}>配置此资产只读授权</Button>
          <Button icon={<Bell size={15} />} disabled={actionsDisabled}
            onClick={() => onOpenConfiguration('notifications', alert)}>配置管理员通知</Button>
        </> : null}
        {alert.incident_id && onOpenIncidents ? <Button aria-label={`查看关联故障 ${alert.title}`} icon={<TicketCheck size={15} />} onClick={onOpenIncidents}>查看关联故障</Button> : null}
      {currentUser.role !== 'auditor' ? <>
        {onOpenTerminal ? <Button aria-label={`打开 ${alert.asset_name || alert.asset_id} SSH`} title={terminalAssetEnabled ? '打开资产 SSH' : '资产未启用或未关联'} disabled={actionsDisabled || !terminalAssetEnabled} icon={<TerminalSquare size={15} />} onClick={() => onOpenTerminal(alert.asset_id)}>打开资产 SSH</Button> : null}
        {nextStatus[alert.status] ? <Button aria-label={`详情推进 ${alert.title}`} disabled={actionsDisabled} icon={<StepForward size={15} />} onClick={() => onAdvance(alert)}>推进处置</Button> : null}
        {!alert.incident_id ? <Button aria-label={`详情转为故障 ${alert.title}`} disabled={actionsDisabled} icon={<TicketCheck size={15} />} onClick={() => onIncident(alert)}>转为故障</Button> : null}
      </> : null}
      </Space>
      <RecoveryVerificationPanel key={`recovery-${alert.id}`} alert={alert} currentUser={currentUser} onUpdated={onUpdated} />
      <DiagnosticJobHistoryPanel key={`diagnostics-${alert.id}`} alert={alert} currentUser={currentUser} onCancel={onCancelJob} />
      <Descriptions size="small" column={2} items={[
        { key: 'source', label: '来源', children: alert.connector_name ?? `连接器 ${alert.connector_id}` },
        { key: 'asset', label: '资产', children: alert.asset_name || alert.asset_id },
        { key: 'severity', label: '严重度', children: severityLabels[alert.severity] },
        { key: 'status', label: '处置状态', children: statusLabels[alert.status] },
        { key: 'occurrences', label: '接收次数', children: `${alert.occurrence_count ?? 1} 次` },
      ]} />
      <section><Typography.Title level={5}>最近诊断证据</Typography.Title>{isActiveDiagnosticJob(alert.latest_job) ? <Typography.Paragraph type="secondary">当前任务尚未完成，下方保留已有历史证据。</Typography.Paragraph> : null}<p>{analysis?.summary ?? '尚未形成研判结论'}</p><Typography.Text type="secondary">置信度：后端未提供量化数据，需人工结合证据判断。</Typography.Text></section>
      <section><Typography.Title level={5}>局限</Typography.Title><Alert type="warning" showIcon message={limitation} /></section>
      <AlertNotificationHistory key={alert.id} currentUser={currentUser} alertId={alert.id} onUpdated={onUpdated} />
      <section><Typography.Title level={5}>证据</Typography.Title><List size="small" locale={{ emptyText: '暂无证据' }} dataSource={evidence} renderItem={([label, value]) => <List.Item><strong>{label}</strong><code>{formatEvidence(value)}</code></List.Item>} /></section>
      <section><Typography.Title level={5}>诊断历史</Typography.Title><List size="small" locale={{ emptyText: '暂无诊断历史' }} dataSource={detail.analyses} renderItem={(item) => <List.Item><List.Item.Meta title={<Space><AnalysisTag status={item.status} errorCode={item.error_code} /><span>{item.summary}</span></Space>} description={`${item.trigger_type} · ${item.operator} · ${new Date(item.started_at).toLocaleString()}`} /></List.Item>} /><Space><Button disabled={detail.analysisPage <= 1} onClick={() => onAnalysisPage(detail.analysisPage - 1)}>上一页诊断</Button><span>第 {detail.analysisPage} 页</span><Button disabled={detail.analyses.length < 20} onClick={() => onAnalysisPage(detail.analysisPage + 1)}>下一页诊断</Button></Space></section>
      <section><Typography.Title level={5}>脱敏载荷</Typography.Title><pre className="redacted-payload">{JSON.stringify(alert.payload, null, 2)}</pre></section>
      <section><Typography.Title level={5}>不可变时间线</Typography.Title><Timeline items={detail.timeline.map((entry) => ({ children: <div className="timeline-entry"><strong>{timelineEventLabels[entry.event_type] ?? '其他事件'} · {timelineStatusLabels[entry.status] ?? '已记录'}</strong><span>{entry.note}</span><small>{entry.operator} · {new Date(entry.created_at).toLocaleString()}</small></div> }))} /></section>
    </div> : null}
  </Drawer>;
}
