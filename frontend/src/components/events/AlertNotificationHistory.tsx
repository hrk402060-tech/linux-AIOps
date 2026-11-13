import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Descriptions, Empty, Modal, Space, Table, Tag, Tooltip, message } from 'antd';
import { CheckCheck, ChevronDown, ChevronRight, RefreshCw, RotateCcw } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import {
  acknowledgeAlertNotification, getAlertNotifications, getNotificationHistory, retryAlertNotification,
  type AlertNotification, type NotificationKind, type NotificationStatus,
} from '../../api/alertNotifications';
import './AlertNotifications.css';

const PAGE_SIZE = 50;
const statuses: Record<NotificationStatus, { label: string; color: string }> = {
  pending: { label: '待发送', color: 'blue' }, blocked: { label: '发送受阻', color: 'gold' },
  sending: { label: '发送中', color: 'processing' }, retry: { label: '等待重试', color: 'gold' },
  sent: { label: '已发送', color: 'green' }, failed: { label: '发送失败', color: 'red' },
};
const kinds: Record<NotificationKind, string> = {
  alert_received: '告警接收', diagnostic_result: '定位结果', asset_mapping_failed: '资产映射失败',
  recovery_result: '恢复验证结果',
};
const formatTime = (value: string | null) => value ? new Date(value).toLocaleString() : '-';

type HistoryProps = {
  currentUser: Pick<AuthUser, 'role'>; alertId?: string; onUpdated?: () => void; refreshVersion?: number;
};
export function AlertNotificationHistory(props: HistoryProps) {
  return <NotificationHistoryContext key={`${props.alertId ?? 'all'}:${props.currentUser.role}`} {...props} />;
}

function NotificationHistoryContext({ currentUser, alertId, onUpdated, refreshVersion }: HistoryProps) {
  const [items, setItems] = useState<AlertNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [authoritative, setAuthoritative] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [acknowledging, setAcknowledging] = useState<AlertNotification | null>(null);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const [messages, holder] = message.useMessage();
  const admin = currentUser.role === 'admin';
  const canAcknowledge = currentUser.role === 'admin' || currentUser.role === 'operator';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; }; }, []);
  const load = async () => {
    const requestId = ++generation.current;
    setLoading(true); setAuthoritative(false); setLoadError('');
    try {
      const result = alertId ? await getAlertNotifications(alertId) : await getNotificationHistory((page - 1) * PAGE_SIZE, PAGE_SIZE);
      if (!mounted.current || requestId !== generation.current) return;
      if (!Array.isArray(result.items) || (alertId && result.items.some((item) => item.alert_id !== alertId))) {
        throw new Error('通知记录与当前告警不匹配，请刷新重试。');
      }
      setItems(result.items); setTotal('total' in result && typeof result.total === 'number' ? result.total : result.items.length);
      setAuthoritative(true);
    } catch (error) {
      if (mounted.current && requestId === generation.current) setLoadError(error instanceof Error ? error.message : '通知记录加载失败');
    } finally { if (mounted.current && requestId === generation.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, [alertId, page, refreshVersion]);
  const update = async (item: AlertNotification, action: 'retry' | 'acknowledge') => {
    const current = items.find((entry) => entry.id === item.id);
    if (!mounted.current || inFlight.current || !authoritative || loading || !current
      || (alertId && current.alert_id !== alertId) || current.acknowledged_at
      || (action === 'retry' ? !admin || !['failed', 'blocked', 'retry'].includes(current.status)
        || current.attempts >= current.max_attempts : !canAcknowledge || current.status !== 'sent')) return;
    const requestId = generation.current;
    inFlight.current = true; setBusyId(item.id);
    try {
      const next = await (action === 'retry' ? retryAlertNotification(item.id) : acknowledgeAlertNotification(item.id));
      if (!mounted.current || requestId !== generation.current) return;
      if (next.id !== item.id || next.alert_id !== current.alert_id) throw new Error('通知操作结果不匹配，请刷新核实。');
      setItems((current) => current.map((entry) => entry.id === next.id ? next : entry));
      setAcknowledging(null);
      messages.success(action === 'retry' ? '重试已提交' : '已记录人工查看确认');
      onUpdated?.();
    } catch (error) {
      if (mounted.current && requestId === generation.current) {
        setAuthoritative(false);
        if (error instanceof TypeError) setAcknowledging(null);
        messages.error(error instanceof Error ? error.message : '通知操作结果待核实，请刷新后确认，不会自动重试');
      }
    } finally { inFlight.current = false; if (mounted.current) setBusyId(null); }
  };
  return <section className="notification-history">{holder}
    <div className="notification-history-heading"><h3>通知发送记录</h3><Tooltip title="刷新通知记录"><Button aria-label="刷新通知记录" disabled={loading || busyId !== null} icon={<RefreshCw size={16} />} onClick={() => void load()} /></Tooltip></div>
    <p className="notification-history-caption">已发送表示邮件服务器接受，不代表管理员已查看或问题已恢复。{alertId ? '当前告警最多展示最近 100 条记录。' : null}</p>
    {loadError ? <Alert type="error" showIcon message={loadError} /> : <div className="table-frame"><Table<AlertNotification>
      rowKey="id" dataSource={items} loading={loading} size="small" scroll={{ x: alertId ? 1080 : 1240 }}
      pagination={alertId ? false : { current: page, pageSize: PAGE_SIZE, total, showSizeChanger: false,
        disabled: loading || busyId !== null || acknowledging !== null, onChange: setPage }}
      locale={{ emptyText: <Empty description="暂无通知记录" /> }} columns={[
        { title: '通知内容', width: 210, render: (_, item) => <div className="notification-kind"><strong>{item.title || kinds[item.kind]}</strong>{item.title ? <small>{kinds[item.kind]}</small> : null}{!alertId ? <small>{item.alert_id || '未匹配资产'}</small> : null}</div> },
        { title: '发送状态', width: 100, render: (_, item) => <Tag color={statuses[item.status].color}>{statuses[item.status].label}</Tag> },
        { title: '尝试次数', width: 85, render: (_, item) => `${item.attempts} / ${item.max_attempts}` },
        { title: '收件人', width: 180, render: (_, item) => <span className="notification-recipients">{item.recipients.join('、') || '待配置'}</span> },
        { title: '失败原因', width: 145, render: (_, item) => <span className="notification-error">{item.error_code === 'notification_not_configured' ? '尚未配置通知通道' : item.error_code === 'notification_disabled' ? '通知通道已停用' : item.error_code || '-'}{item.status === 'failed' ? <small>需人工升级处理</small> : null}</span> },
        { title: '时间', width: 180, render: (_, item) => <div className="notification-times"><span>创建 {formatTime(item.created_at)}</span><span>发送 {formatTime(item.sent_at)}</span>{item.next_attempt_at ? <span>重试 {formatTime(item.next_attempt_at)}</span> : null}</div> },
        { title: '人工查看', width: 165, render: (_, item) => item.acknowledged_at ? <div className="notification-times"><span>{item.acknowledged_by}</span><span>{formatTime(item.acknowledged_at)}</span></div> : <span>{item.status === 'sent' ? '未确认查看' : '-'}</span> },
        { title: '操作', width: 105, fixed: 'right', render: (_, item) => <Space size={4}>
          {admin && ['failed', 'blocked', 'retry'].includes(item.status) ? <Tooltip title={item.attempts >= item.max_attempts ? '重试次数已达上限，需人工升级处理' : '重试发送'}><Button aria-label={`重试通知 ${item.id}`} icon={<RotateCcw size={15} />} loading={busyId === item.id} disabled={!authoritative || loading || busyId !== null || item.attempts >= item.max_attempts} onClick={() => void update(item, 'retry')} /></Tooltip> : null}
          {canAcknowledge && item.status === 'sent' && !item.acknowledged_at ? <Tooltip title="确认已查看"><Button aria-label={`确认已查看通知 ${item.id}`} icon={<CheckCheck size={15} />} disabled={!authoritative || loading || busyId !== null} onClick={() => setAcknowledging(item)} /></Tooltip> : null}
        </Space> },
      ]}
      expandable={{ expandedRowRender: (item) => <NotificationDetails item={item} />, columnWidth: 44, expandIcon: ({ expanded, onExpand, record }) => <Tooltip title={expanded ? '收起通知详情' : '查看通知详情'}><Button type="text" size="small" aria-label={`${expanded ? '收起' : '查看'}通知详情 ${record.id}`} icon={expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />} onClick={(event) => onExpand(record, event)} /></Tooltip> }}
    /></div>}
    <Modal title="人工查看确认" open={acknowledging !== null} okText="确认已查看" cancelText="取消" confirmLoading={busyId !== null} onOk={() => { if (acknowledging) void update(acknowledging, 'acknowledge'); }} onCancel={() => { if (!inFlight.current) setAcknowledging(null); }} okButtonProps={{ disabled: !canAcknowledge || !authoritative || loading || !acknowledging || busyId !== null }} cancelButtonProps={{ disabled: busyId !== null }} closable={busyId === null} maskClosable={busyId === null} keyboard={busyId === null}>
      <p>将记录当前用户和确认时间；不会推进告警状态或标记问题已恢复。</p>
      {!authoritative && !loading ? <p>操作结果待核实，请取消并刷新通知记录后再操作。</p> : null}
    </Modal>
  </section>;
}

function NotificationDetails({ item }: { item: AlertNotification }) {
  const locators = Object.entries(item.asset_locator ?? {});
  return <div className="notification-details">
    <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
      { key: 'title', label: '告警标题', children: item.title || kinds[item.kind] },
      { key: 'source', label: '告警来源', children: item.source || '-' },
      { key: 'external', label: '外部告警 ID', children: item.external_id || '-' },
      { key: 'alert', label: '关联告警 ID', children: item.alert_id || '未建立告警记录' },
      ...locators.map(([key, value]) => ({ key: `locator-${key}`, label: `资产标识 ${key}`, children: value })),
    ]} />
  </div>;
}
