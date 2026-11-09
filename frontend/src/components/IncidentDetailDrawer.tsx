import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Form, Input, Modal, Select, Space, Table, Tag, Timeline, message } from 'antd';
import { Plus, RefreshCw } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import {
  createIncidentTicket, getIncident, getIncidentTickets, getIncidentTimeline,
  updateIncidentTicket, type Incident, type IncidentSeverity, type IncidentTicket,
  type IncidentTicketStatus, type IncidentTimelineEntry,
} from '../api/incidents';
import { getAlertTimeline, getExternalAlert, type AlertTimelineEntry, type ExternalAlert } from '../api/externalAlerts';
import { RecoveryVerificationPanel } from './events/RecoveryVerificationPanel';
import { AlertNotificationHistory } from './events/AlertNotificationHistory';
import { getNotificationStage } from './events/AlertWorkflow';
import { isActiveDiagnosticJob } from './events/DiagnosticJobTag';

const statuses = {
  open: '待确认', acknowledged: '已确认', in_progress: '处理中',
  resolved: '人工登记完成', closed: '人工关闭',
};
const severities = { critical: '严重', high: '高危', medium: '中危', low: '低危' };
const colors = { critical: 'red', high: 'volcano', medium: 'gold', low: 'blue' };
const analysisLabels = {
  running: '研判中', completed: '研判完成', partial: '部分完成', failed: '研判失败', cancelled: '已取消',
};
const alertTimelineLabels: Record<string, string> = {
  ingested: '告警接入', duplicate: '重复接收', reopened: '告警复发',
  status: '状态变更', diagnostic_queued: '诊断入队', diagnostic_finished: '诊断完成',
  incident_created: '转为故障', recovery_started: '开始恢复验证',
  recovery_observing: '恢复验证观察中', recovery_verified: '恢复检查通过',
  recovery_failed: '恢复检查失败', recovery_blocked: '恢复验证受阻',
  recovery_evaluated: '恢复验证', recovery_cancelled: '取消恢复验证',
};
const alertStatusLabels: Record<string, string> = {
  open: '待确认', acknowledged: '已确认', in_progress: '处理中',
  resolved: '人工登记完成', closed: '人工已关闭', queued: '排队中',
  running: '执行中', completed: '已完成', partial: '部分完成',
  failed: '失败', cancelled: '已取消', observing: '待观察',
  verified: '检查通过', blocked: '验证受阻',
};
const slaLabels = { on_track: '正常', at_risk: '临近', breached: '已超时', met: '已达标' };
const slaColors = { on_track: 'green', at_risk: 'gold', breached: 'red', met: 'blue' };
function externalAlertId(source: string): string | null {
  const id = source.startsWith('alert:') ? source.slice(6) : source;
  return id && !id.includes(':') ? id : null;
}

export function IncidentDetailDrawer({ incident, currentUser, onClose }: {
  incident: Incident | null; currentUser: Pick<AuthUser, 'role'>; onClose: () => void;
}) {
  return <Drawer title={incident ? `故障详情 · ${incident.title}` : '故障详情'}
    open={incident !== null} onClose={onClose} width={640} destroyOnHidden>
    {incident ? <IncidentContext key={`${incident.id}:${currentUser.role}`}
      incident={incident} currentUser={currentUser} /> : null}
  </Drawer>;
}

function IncidentContext({ incident, currentUser }: {
  incident: Incident; currentUser: Pick<AuthUser, 'role'>;
}) {
  const [detail, setDetail] = useState<Incident | null>(null);
  const [alert, setAlert] = useState<ExternalAlert | null>(null);
  const [alertTimeline, setAlertTimeline] = useState<AlertTimelineEntry[]>([]);
  const [history, setHistory] = useState<IncidentTimelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [linkError, setLinkError] = useState('');
  const [alertTimelineError, setAlertTimelineError] = useState('');
  const [tickets, setTickets] = useState<IncidentTicket[] | null>(null);
  const [ticketLoading, setTicketLoading] = useState(false);
  const [ticketOpen, setTicketOpen] = useState(false);
  const [ticketTarget, setTicketTarget] = useState<IncidentTicket | null>(null);
  const [ticketSubmitting, setTicketSubmitting] = useState(false);
  const [ticketMessages, ticketHolder] = message.useMessage();
  const [ticketForm] = Form.useForm<{
    title: string; description: string; priority: IncidentSeverity; due_at?: string;
  }>();
  const generation = useRef(0);
  const mounted = useRef(true);
  const timelineAbort = useRef<AbortController | null>(null);
  useEffect(() => () => {
    mounted.current = false;
    generation.current += 1;
    timelineAbort.current?.abort();
  }, []);
  const load = useCallback(async () => {
    const request = ++generation.current;
    timelineAbort.current?.abort();
    timelineAbort.current = null;
    setLoading(true); setError(''); setLinkError(''); setAlertTimelineError('');
    setDetail(null); setAlert(null); setAlertTimeline([]); setHistory([]);
    setTickets(null); setTicketTarget(null);
    try {
      const [current, entries] = await Promise.all([getIncident(incident.id), getIncidentTimeline(incident.id)]);
      if (request !== generation.current) return;
      if (current.id !== incident.id) throw new Error('Incident identity mismatch');
      setDetail(current); setHistory(entries);
      if (current.alert_id) {
        const alertId = externalAlertId(current.alert_id);
        if (!alertId) {
          setLinkError('关联记录不是外部告警，恢复验证不可用。');
          return;
        }
        try {
          const linked = await getExternalAlert(alertId);
          if (!mounted.current || request !== generation.current) return;
          if (linked.id !== alertId || linked.asset_id !== current.asset_id
            || (linked.incident_id && linked.incident_id !== current.id)) {
            setLinkError('关联告警与当前故障不匹配，已禁止恢复验证。');
          } else {
            setAlert(linked);
            const controller = new AbortController();
            timelineAbort.current = controller;
            void getAlertTimeline(alertId, controller.signal).then((entries) => {
              if (!mounted.current || request !== generation.current) return;
              if (entries.some((entry) => entry.alert_id !== alertId)) {
                throw new Error('关联告警时间线与当前告警不匹配');
              }
              setAlertTimeline(entries);
            }).catch(() => {
              if (mounted.current && request === generation.current) {
                setAlertTimelineError('关联告警时间线暂不可用，请刷新重试。');
              }
            });
          }
        } catch {
          if (request === generation.current) setLinkError('关联告警暂不可用，请刷新重试；不会自动发起验证。');
        }
      }
    } catch {
      if (request === generation.current) setError('故障详情加载失败，请刷新重试；当前不可进行恢复验证。');
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [incident.id, currentUser.role]);
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, [load]);
  const loadTickets = async () => {
    setTicketLoading(true);
    try { setTickets(await getIncidentTickets(incident.id)); }
    catch (error) { ticketMessages.error(error instanceof Error ? error.message : '工单加载失败'); }
    finally { setTicketLoading(false); }
  };
  const submitTicket = async () => {
    const values = await ticketForm.validateFields();
    setTicketSubmitting(true);
    try {
      await createIncidentTicket(incident.id, values);
      ticketMessages.success('工单已创建');
      setTicketOpen(false); ticketForm.resetFields(); await loadTickets();
    } catch (error) { ticketMessages.error(error instanceof Error ? error.message : '工单创建失败'); }
    finally { setTicketSubmitting(false); }
  };
  const advanceTicket = async (status: IncidentTicketStatus) => {
    if (!ticketTarget) return;
    setTicketSubmitting(true);
    try {
      await updateIncidentTicket(ticketTarget.id, status, '人工更新工单');
      ticketMessages.success('工单状态已更新');
      setTicketTarget(null); await loadTickets();
    } catch (error) { ticketMessages.error(error instanceof Error ? error.message : '工单更新失败'); }
    finally { setTicketSubmitting(false); }
  };

  return <div className="detail-stack">{ticketHolder}
    <div><Button aria-label="刷新故障详情" icon={<RefreshCw size={16} />}
      loading={loading} disabled={loading} onClick={() => void load()} /></div>
    {loading ? <p>正在加载故障详情...</p> : null}
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {detail ? <>
      <section className="ops-panel"><h3>基本信息</h3><div className="detail-grid">
        <span>状态</span><strong>{statuses[detail.status]}</strong>
        <span>级别</span><strong><Tag color={colors[detail.severity]}>{severities[detail.severity]}</Tag></strong>
        <span>资产</span><strong>{detail.asset_name} · {detail.asset_id}</strong>
        <span>创建人</span><strong>{detail.operator}</strong>
        <span>创建时间</span><strong>{new Date(detail.created_at).toLocaleString()}</strong>
        <span>更新时间</span><strong>{new Date(detail.updated_at ?? detail.created_at).toLocaleString()}</strong>
        <span>SLA</span><strong><Tag color={slaColors[detail.sla_status ?? 'on_track']}>{slaLabels[detail.sla_status ?? 'on_track']}</Tag>
          {detail.sla_due_at ? ` · 到期 ${new Date(detail.sla_due_at).toLocaleString()}` : null}</strong>
      </div><p>{detail.description}</p></section>
      <section className="ops-panel"><h3>关联上下文</h3>
        {linkError ? <Alert type="warning" showIcon message={linkError} /> : null}
        {!detail.alert_id ? <p>此故障未关联外部告警，暂无关联恢复验证。</p> : null}
        {alert ? <><p><strong>告警：</strong>{alert.title} · {statuses[alert.status]}</p>
          <p><strong>接收时间：</strong>{new Date(alert.received_at).toLocaleString()}</p>
          <p><strong>诊断：</strong>{alert.latest_analysis
            ? `${analysisLabels[alert.latest_analysis.status] ?? '未知状态'} · ${alert.latest_analysis.summary}`
            : '暂无诊断结果'}</p></> : null}
      </section>
      {alert ? <section className="ops-panel" aria-label="关联告警时间线">
        <h3>关联告警时间线</h3>
        {alertTimelineError ? <Alert type="warning" showIcon message={alertTimelineError} /> : alertTimeline.length ? <Timeline items={alertTimeline.map((entry) => ({
          children: <div className="timeline-entry">
            <strong>{alertTimelineLabels[entry.event_type] ?? '其他事件'} · {alertStatusLabels[entry.status] ?? entry.status}</strong>
            <span>{entry.note}</span>
            <small>{entry.operator} · {new Date(entry.created_at).toLocaleString()}</small>
          </div>,
        }))} /> : <p>暂无告警时间线记录。</p>}
      </section> : null}
      <section className="ops-panel" aria-label="关联工单">
        <div className="panel-heading"><div><h3>关联工单</h3><span>工单状态与故障 SLA 分开记录</span></div>
          <Space><Button loading={ticketLoading} onClick={() => void loadTickets()}>加载工单</Button>{currentUser.role !== 'auditor' ? <Button type="primary" icon={<Plus size={15} />} onClick={() => setTicketOpen(true)}>新建工单</Button> : null}</Space>
        </div>
        {tickets ? <Table<IncidentTicket> rowKey="id" size="small" pagination={false} dataSource={tickets} locale={{ emptyText: '暂无关联工单' }} columns={[
          { title: '工单', render: (_, item) => <div className="stacked-cell"><strong>{item.title}</strong><small>{item.description}</small></div> },
          { title: '优先级', dataIndex: 'priority', render: (value: IncidentSeverity) => <Tag color={colors[value]}>{severities[value]}</Tag> },
          { title: '状态', dataIndex: 'status', render: (value: IncidentTicketStatus) => <Tag>{value === 'open' ? '待处理' : value === 'in_progress' ? '处理中' : value === 'resolved' ? '已解决' : '已关闭'}</Tag> },
          ...(currentUser.role !== 'auditor' ? [{ title: '操作', width: 90, render: (_: unknown, item: IncidentTicket) => item.status !== 'closed' ? <Button type="link" onClick={() => setTicketTarget(item)}>推进</Button> : null }] : []),
        ]} /> : <Alert type="info" showIcon message="工单尚未加载" description="点击“加载工单”读取当前故障的处理记录。" />}
      </section>
      {!loading && alert && !error && !linkError ? <>
        <IncidentNotifications alert={alert} currentUser={currentUser} onUpdated={() => void load()} />
        <RecoveryVerificationPanel key={`${alert.id}:${currentUser.role}`} alert={alert}
          currentUser={currentUser} onUpdated={() => void load()} />
      </> : null}
      <section className="ops-panel"><h3>处置时间线</h3>
        <Timeline items={history.map((entry) => ({ children: <div className="timeline-entry">
          <strong>{statuses[entry.status]}</strong><span>{entry.note}</span>
          <small>{entry.operator} · {new Date(entry.created_at).toLocaleString()}</small>
        </div> }))} />
      </section>
      <Modal title="新建工单" open={ticketOpen} confirmLoading={ticketSubmitting} okText="创建工单" onOk={() => void submitTicket()} onCancel={() => setTicketOpen(false)} destroyOnHidden>
        <Form form={ticketForm} layout="vertical" initialValues={{ priority: detail.severity }}>
          <Form.Item name="title" label="工单标题" rules={[{ required: true, min: 3 }]}><Input maxLength={160} /></Form.Item>
          <Form.Item name="description" label="处理要求" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={3} maxLength={5000} /></Form.Item>
          <Form.Item name="priority" label="优先级" rules={[{ required: true }]}><Select options={Object.entries(severities).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="due_at" label="SLA 到期时间"><Input type="datetime-local" /></Form.Item>
        </Form>
      </Modal>
      <Modal title="推进工单" open={ticketTarget !== null} confirmLoading={ticketSubmitting} onCancel={() => setTicketTarget(null)} footer={null} destroyOnHidden>
        {ticketTarget ? <Space direction="vertical" style={{ width: '100%' }}>
          <p><strong>{ticketTarget.title}</strong></p>
          {(['in_progress', 'resolved', 'closed'] as IncidentTicketStatus[]).filter((value) => value !== ticketTarget.status).map((value) => (
            <Button key={value} block onClick={() => void advanceTicket(value)}>
              {value === 'in_progress' ? '开始处理' : value === 'resolved' ? '标记已解决' : '关闭工单'}
            </Button>
          ))}
        </Space> : null}
      </Modal>
    </> : null}
  </div>;
}

function IncidentNotifications({ alert, currentUser, onUpdated }: {
  alert: ExternalAlert; currentUser: Pick<AuthUser, 'role'>; onUpdated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const stage = getNotificationStage(alert.notification_summary, alert.latest_analysis, isActiveDiagnosticJob(alert.latest_job));
  return <section aria-label="管理员通知"><h3>管理员通知</h3>
    <Tag color={stage.tone === 'blocked' ? 'gold' : stage.tone === 'complete' ? 'green' : 'blue'}>{stage.value}</Tag>
    <p>{stage.detail}</p>
    <Button aria-label="查看管理员通知记录" onClick={() => setOpen((value) => !value)}>
      {open ? '收起通知记录' : '查看通知记录'}
    </Button>
    {open ? <AlertNotificationHistory currentUser={currentUser} alertId={alert.id} onUpdated={onUpdated} /> : null}
  </section>;
}
