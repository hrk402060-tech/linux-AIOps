import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Descriptions, Drawer, Empty, Input, InputNumber, List, Modal, Pagination, Segmented, Select, Space, Tag, Typography } from 'antd';
import { Activity, Ban, Eye, RefreshCw } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import type { ExternalAlert } from '../../api/externalAlerts';
import { actOnRecovery, createRecovery, getRecoveryEvaluations, getRecoveryHistory, RecoveryRequestError,
  type RecoveryEvaluationHistory, type RecoveryHistory, type RecoveryMetric, type RecoveryStatus, type RecoveryVerification } from '../../api/recovery';

export const recoveryLabels: Record<RecoveryStatus, string> = {
  observing: '待观察', verified: '检查通过', failed: '检查失败', blocked: '验证受阻', cancelled: '已取消',
};
const colors: Record<RecoveryStatus, string> = { observing: 'blue', verified: 'green', failed: 'red', blocked: 'gold', cancelled: 'default' };
const date = (value: string) => Number.isNaN(new Date(value).getTime()) ? '时间无效' : new Date(value).toLocaleString();
type Target = { action: 'create' } | { action: 'evaluate' | 'cancel'; item: RecoveryVerification };
const confirmations = { create: '发起恢复验证', evaluate: '核验恢复证据', cancel: '取消恢复验证' };
const metricLabels: Record<RecoveryMetric, string> = { availability: '在线状态', cpu_percent: 'CPU 使用率',
  memory_percent: '内存使用率', swap_percent: 'Swap 使用率', disk_percent: '磁盘使用率',
  inode_percent: 'inode 使用率', load_1m: '1 分钟负载' };
const sources: Record<string, string> = { monitoring_snapshot: '监控采样', http_probe_run: 'API 拨测记录' };
const statuses: Record<string, string> = { online: '在线', offline: '离线', healthy: '健康', unhealthy: '异常', error: '采集失败' };
const probeErrors: Record<string, string> = { timeout: '请求超时', tls_error: 'TLS 校验失败',
  connection_error: '连接失败', http_status_mismatch: 'HTTP 状态码不符合预期', unknown_error: '未知错误' };
const safeMonitorError = '监控采集异常，原始错误请在受控日志中核查。';
const isTraceId = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const mapped = (labels: Record<string, string>, value: unknown, fallback: string) =>
  typeof value === 'string' && Object.prototype.hasOwnProperty.call(labels, value) ? labels[value] : fallback;
const scalar = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? String(value)
  : typeof value === 'string' ? value.slice(0, 160) : '未提供';
function RecoveryEvidence({ entry, metrics }: { entry: Record<string, unknown>; metrics: RecoveryHistory['options']['metrics'] }) {
  const metricLabel = typeof entry.metric === 'string'
    ? metrics.find((option) => option.value === entry.metric)?.label ?? mapped(metricLabels, entry.metric, '未知指标')
    : '不适用';
  return <div style={{ width: '100%', overflowWrap: 'anywhere' }}><Descriptions size="small" column={1} items={[
    { key: 'source', label: '证据来源', children: mapped(sources, entry.source, '未知来源') },
    { key: 'at', label: '采集时间', children: typeof entry.at === 'string' ? date(entry.at) : '未提供' },
    { key: 'status', label: '采集状态', children: mapped(statuses, entry.status, '未知状态') },
    { key: 'metric', label: '检查指标', children: metricLabel },
    { key: 'value', label: '采样值', children: scalar(entry.value) },
    { key: 'threshold', label: '恢复阈值（不高于）', children: scalar(entry.threshold) },
    ...(entry.http_status !== undefined ? [{ key: 'http', label: 'HTTP 状态码', children: scalar(entry.http_status) }] : []),
    ...(typeof entry.latency_ms === 'number' && Number.isFinite(entry.latency_ms)
      ? [{ key: 'latency', label: '响应耗时', children: `${scalar(entry.latency_ms)} 毫秒` }] : []),
    ...(entry.error_code ? [{ key: 'error', label: '失败原因', children: mapped(probeErrors, entry.error_code, '未知错误') }] : []),
    ...(entry.error_message ? [{ key: 'message', label: '采集说明', children: safeMonitorError }] : []),
  ]} />{entry.id !== undefined ? <details><summary>技术详情</summary><Descriptions size="small" column={1}
    items={[{ key: 'id', label: '证据编号', children: scalar(entry.id) },
      ...(entry.trace_id ? [{ key: 'trace', label: '链路编号', children: isTraceId(entry.trace_id) ? entry.trace_id : '未知链路' }] : [])]} /></details> : null}</div>;
}

type RecoveryPanelProps = {
  alert: ExternalAlert; currentUser: Pick<AuthUser, 'role'>; onUpdated: () => void;
};
export function RecoveryVerificationPanel(props: RecoveryPanelProps) {
  const { alert, currentUser } = props;
  return <RecoveryVerificationContext key={`${alert.id}:${alert.received_at}:${currentUser.role}`} {...props} />;
}

function RecoveryVerificationContext({ alert, currentUser, onUpdated }: RecoveryPanelProps) {
  const [data, setData] = useState<RecoveryHistory | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [authoritative, setAuthoritative] = useState(false);
  const [error, setError] = useState('');
  const [target, setTarget] = useState<Target | null>(null);
  const [working, setWorking] = useState(false);
  const [kind, setKind] = useState<'monitor' | 'probe'>('monitor');
  const [metric, setMetric] = useState<RecoveryMetric>('availability');
  const [threshold, setThreshold] = useState<number | null>(80);
  const [probe, setProbe] = useState<string>();
  const [minutes, setMinutes] = useState<number | null>(15);
  const [gap, setGap] = useState<number | null>(360);
  const [reason, setReason] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [historyTarget, setHistoryTarget] = useState<RecoveryVerification | null>(null);
  const [evaluations, setEvaluations] = useState<RecoveryEvaluationHistory | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const generation = useRef(0);
  const historyGeneration = useRef(0);
  const pending = useRef(false);
  const mounted = useRef(true);
  const scopeKey = `${alert.id}:${alert.received_at}:${currentUser.role}`;
  const scope = useRef(0);
  const canWrite = currentUser.role === 'admin' || currentUser.role === 'operator';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; historyGeneration.current += 1; }; }, []);
  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true); setAuthoritative(false); setError('');
    try {
      const result = await getRecoveryHistory(alert.id, (page - 1) * 10);
      if (mounted.current && request === generation.current) { setData(result); setAuthoritative(true); }
    } catch (failure) {
      if (mounted.current && request === generation.current) setError(failure instanceof Error ? failure.message : '恢复验证加载失败');
    } finally { if (mounted.current && request === generation.current) setLoading(false); }
  }, [alert.id, page, currentUser.role]);
  useEffect(() => { void load(); }, [load]);
  const loadEvaluations = useCallback(async (item: RecoveryVerification, nextPage = 1) => {
    const request = ++historyGeneration.current;
    setHistoryTarget(item); setHistoryPage(nextPage); setHistoryLoading(true); setHistoryError(''); setEvaluations(null);
    try {
      const result = await getRecoveryEvaluations(alert.id, item.id, (nextPage - 1) * 10);
      if (mounted.current && request === historyGeneration.current) setEvaluations(result);
    } catch (failure) {
      if (mounted.current && request === historyGeneration.current) setHistoryError(failure instanceof Error ? failure.message : '评估记录加载失败');
    } finally { if (mounted.current && request === historyGeneration.current) setHistoryLoading(false); }
  }, [alert.id]);
  const clearDraft = () => {
    setReason(''); setConfirmation(''); setKind('monitor'); setMetric('availability');
    setThreshold(80); setProbe(undefined); setMinutes(15); setGap(360);
  };
  useEffect(() => {
    scope.current += 1; clearDraft(); setTarget(null); setData(null);
    historyGeneration.current += 1; setHistoryTarget(null); setEvaluations(null); setHistoryError('');
  }, [scopeKey]);
  const open = (value: Target) => { clearDraft(); setError(''); setTarget(value); };
  const close = () => { if (!pending.current) { setTarget(null); clearDraft(); } };
  const validConfig = minutes !== null && Number.isInteger(minutes) && minutes >= 1 && minutes <= 60
    && gap !== null && Number.isInteger(gap) && gap >= 30 && gap <= 600 && gap <= minutes * 30
    && (kind === 'probe' ? Boolean(probe) : metric === 'availability' || (threshold !== null && Number.isFinite(threshold)
      && threshold >= 0 && threshold <= (metric === 'load_1m' ? 1000 : 100)));
  const expected = target ? confirmations[target.action] : '';
  const confirmed = confirmation.trim() === expected && reason.trim().length >= 3 && reason.trim().length <= 1000;
  const submit = async () => {
    if (!target || pending.current || !canWrite || !confirmed || !authoritative || (target.action === 'create' && !validConfig)) return;
    if (target.action !== 'create' && target.item.is_current === false) return;
    pending.current = true; setWorking(true); setError('');
    const requestScope = scope.current;
    try {
      if (target.action === 'create') {
        await createRecovery(alert.id, { kind, ...(kind === 'probe' ? { probe_id: probe } : { metric,
          ...(metric === 'availability' ? {} : { threshold: threshold! }) }),
          observation_minutes: minutes!, max_gap_seconds: gap!, confirmation: alert.id, reason: reason.trim() });
      } else {
        await actOnRecovery(alert.id, target.item, target.action, {
          expected_version: target.item.version, confirmation: alert.id, reason: reason.trim(),
        });
      }
      if (!mounted.current || scope.current !== requestScope) return;
      setTarget(null); clearDraft();
      await load();
      if (mounted.current && scope.current === requestScope) onUpdated();
    } catch (failure) {
      if (!mounted.current || scope.current !== requestScope) return;
      const detail = failure instanceof Error ? failure.message : '请求结果待核实，请刷新后确认，不会自动重试';
      if (failure instanceof RecoveryRequestError && [403, 409].includes(failure.status)) {
        setTarget(null); await load();
        if (mounted.current && scope.current === requestScope) onUpdated();
      }
      if (!mounted.current || scope.current !== requestScope) return;
      if (!(failure instanceof RecoveryRequestError) || failure.status >= 500) {
        setTarget(null); setAuthoritative(false);
      }
      if (mounted.current) setError(detail);
    } finally { pending.current = false; if (mounted.current) setWorking(false); }
  };
  const active = data?.options.active_verification || data?.items.some((item) => item.is_current !== false && item.status === 'observing');
  const canCreate = authoritative && data?.options.asset_enabled && ['in_progress', 'resolved', 'closed'].includes(alert.status) && !active;
  const closeHistory = () => { historyGeneration.current += 1; setHistoryTarget(null); setEvaluations(null); };
  return <section aria-label="恢复验证" style={{ margin: '16px 0' }}>
    <Space wrap style={{ marginBottom: 8 }}><Typography.Title level={5} style={{ margin: 0 }}>恢复验证</Typography.Title>
      <Button aria-label="刷新恢复验证" icon={<RefreshCw size={15} />} loading={loading} disabled={working || target !== null} onClick={() => void load()} />
      {canWrite ? <Button icon={<Activity size={15} />} disabled={!canCreate || working || loading} onClick={() => open({ action: 'create' })}>发起恢复验证</Button> : null}
    </Space>
    <Typography.Paragraph type="secondary">仅验证指定检查与观察窗口，不代表整个服务恢复；不自动关闭告警，不触发远程采集或修复。</Typography.Paragraph>
    {error ? <Alert showIcon type="error" message={error} style={{ marginBottom: 8 }} /> : null}
    <List loading={loading} size="small" dataSource={data?.items ?? []} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无恢复验证" /> }} renderItem={(item) => <List.Item actions={[
      <Button key="history" aria-label="查看评估记录" icon={<Eye size={15} />} onClick={() => void loadEvaluations(item)} />,
      ...(canWrite && item.is_current !== false && item.status === 'observing' ? [<Button key="evaluate" disabled={working || loading || !authoritative || target !== null} onClick={() => open({ action: 'evaluate', item })}>核验当前证据</Button>,
        <Button key="cancel" disabled={working || loading || !authoritative || target !== null} icon={<Ban size={15} />} onClick={() => open({ action: 'cancel', item })}>取消验证</Button>] : []),
    ]}><List.Item.Meta title={<Space wrap>{item.is_current === false ? <Tag>历史轮次</Tag> : null}<Tag color={colors[item.status]}>{recoveryLabels[item.status]}</Tag><strong>{item.kind === 'probe' ? data?.options.probes.find((option) => option.id === item.probe_id)?.name ?? '资产 API 拨测' : data?.options.metrics.find((option) => option.value === item.metric)?.label ?? metricLabels[item.metric as RecoveryMetric] ?? '未知指标'}{item.threshold !== null ? ` ≤ ${item.threshold}` : ''}</strong></Space>}
      description={<><div>观察起点 {date(item.started_at)} · 最早完成 {date(item.deadline_at)} · 最大间隔 {item.max_gap_seconds} 秒</div>{item.last_evaluation ? <div>实际核验至 {date(item.last_evaluation.evaluated_at)}</div> : null}<div>{item.operator} · {item.reason}</div><div>{item.last_evaluation?.summary ?? '观察窗口已登记，等待样本与人工核验'}</div></>} /></List.Item>} />
    {data && data.total > 10 ? <Pagination size="small" current={page} pageSize={10} total={data.total} disabled={working || loading || target !== null} showSizeChanger={false} onChange={setPage} /> : null}
    <Modal title={target?.action === 'create' ? '发起恢复验证' : target?.action === 'cancel' ? '取消恢复验证' : '核验恢复证据'} open={target !== null}
      onCancel={close} onOk={() => void submit()} confirmLoading={working}
      okText={target?.action === 'create' ? '确认发起' : target?.action === 'cancel' ? '确认取消' : '确认核验'} cancelText="返回"
      okButtonProps={{ disabled: !canWrite || working || !confirmed || !authoritative || (target?.action === 'create' && !validConfig) }}
      cancelButtonProps={{ disabled: working }} maskClosable={!working} closable={!working} destroyOnHidden>
      {target?.action === 'create' ? <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}>
        <Segmented aria-label="验证来源" value={kind} options={[{ value: 'monitor', label: '监控指标' }, { value: 'probe', label: 'API 拨测' }]} onChange={(value) => setKind(value as 'monitor' | 'probe')} disabled={working} />
        {kind === 'monitor' ? <><Select aria-label="验证指标" style={{ width: '100%' }} value={metric} options={data?.options.metrics} disabled={working} onChange={setMetric} />
          {metric !== 'availability' ? <label>恢复阈值 <InputNumber aria-label="恢复阈值" value={threshold} min={0} max={metric === 'load_1m' ? 1000 : 100} disabled={working} onChange={setThreshold} /></label> : null}</>
          : <Select aria-label="验证拨测" style={{ width: '100%' }} value={probe} options={data?.options.probes.map((item) => ({ value: item.id, label: item.name }))} placeholder="选择已启用的资产拨测" disabled={working} onChange={setProbe} />}
        <label>观察窗口（分钟） <InputNumber aria-label="观察窗口" value={minutes} min={1} max={60} precision={0} disabled={working} onChange={setMinutes} /></label>
        <label>最大采样间隔（秒） <InputNumber aria-label="最大采样间隔" value={gap} min={30} max={Math.min(600, (minutes ?? 15) * 30)} precision={0} disabled={working} onChange={setGap} /></label>
      </Space> : null}
      <p>请输入确认内容：<Typography.Text code>{expected}</Typography.Text></p>
      <Input aria-label="恢复验证确认文本" value={confirmation} disabled={working} maxLength={100} onChange={(event) => setConfirmation(event.target.value)} />
      <label>验证原因<Input.TextArea aria-label="验证原因" rows={3} maxLength={1000} value={reason} disabled={working} onChange={(event) => setReason(event.target.value)} /></label>
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
        请填写 3～1000 个字符，首尾空格不计入；操作原因将保留在审计记录中。
      </Typography.Paragraph>
      {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} /> : null}
    </Modal>
    <Drawer title="恢复评估记录" open={historyTarget !== null} width="min(640px, 100vw)" onClose={closeHistory}>
      {historyError ? <Alert type="error" message={historyError} action={<Button onClick={() => { if (historyTarget) void loadEvaluations(historyTarget, historyPage); }}>重试</Button>} /> : null}
      <List loading={historyLoading} dataSource={evaluations?.items ?? []} locale={{ emptyText: '暂无评估记录' }} renderItem={(item) => <List.Item><div style={{ width: '100%' }}>
        <Space wrap><Tag color={colors[item.outcome]}>{recoveryLabels[item.outcome]}</Tag><span>{date(item.evaluated_at)} · {item.operator}</span></Space><p>{item.summary}</p>
        <details><summary>技术详情</summary><Descriptions size="small" column={1} items={[
          { key: 'trace', label: '关联编号', children: item.trace_id }, { key: 'verification', label: '验证编号', children: item.verification_id },
        ]} /></details>
        <List size="small" dataSource={item.evidence} renderItem={(entry) => <List.Item><RecoveryEvidence entry={entry} metrics={data?.options.metrics ?? []} /></List.Item>} />
      </div></List.Item>} />
      {evaluations && evaluations.total > 10 ? <Pagination size="small" current={historyPage} total={evaluations.total} pageSize={10} showSizeChanger={false} onChange={(value) => { if (historyTarget) void loadEvaluations(historyTarget, value); }} /> : null}
    </Drawer>
  </section>;
}
