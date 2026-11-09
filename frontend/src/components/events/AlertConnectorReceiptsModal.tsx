import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Segmented, Space, Spin, Table, Tag } from 'antd';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import {
  getAlertConnectorReceipts, type AlertConnector, type ConnectorReceipt,
  type ConnectorReceiptPage, type ConnectorReceiptResult,
} from '../../api/externalAlerts';

type ReceiptFilter = 'all' | ConnectorReceiptResult;
interface Props {
  target: AlertConnector | null;
  enabled: boolean;
  onClose: () => void;
}

const pageSize = 20;
const outcomeLabels: Record<ConnectorReceipt['outcome'], string> = {
  created: '已创建告警', duplicate: '重复告警已接收',
  reopened: '已重新打开告警', rejected: '接收被拒绝',
};
const loadError = '接收记录加载失败，请刷新后重试';
const formatTime = (value: string): string => new Date(value).toLocaleString('zh-CN');
const failureMessages: Record<string, string> = {
  signature_invalid: '签名不匹配',
  nonce_invalid: '请求随机数无效',
  timestamp_invalid: '时间戳无效',
  timestamp_expired: '时间戳超出允许窗口',
  replay_detected: '重复请求已拒绝',
  key_unavailable: '接入密钥暂不可用',
  connector_disabled: '连接器已停用',
  payload_invalid: '告警格式无效',
  payload_too_large: '请求超过64KiB',
  asset_not_found: '未匹配服务器资产',
  asset_ambiguous: '资产信息不唯一或冲突',
  asset_disabled: '资产未启用',
  evidence_conflict: '接收证据冲突',
  storage_unavailable: '接收存储暂不可用',
  request_rejected: '请求被拒绝',
};

export function getReceiptFailureMessage(code: string | null): string {
  return code && Object.hasOwn(failureMessages, code) ? failureMessages[code] : '接收失败';
}

async function requestPage(id: string, page: number, filter: ReceiptFilter): Promise<ConnectorReceiptPage> {
  const offset = (page - 1) * pageSize;
  const result = await getAlertConnectorReceipts(id, offset, pageSize, filter === 'all' ? undefined : filter);
  const valid = Array.isArray(result.items) && result.items.length <= pageSize
    && Number.isSafeInteger(result.total) && result.total >= result.items.length
    && result.offset === offset && result.limit === pageSize
    && result.items.every((item) => item.connector_id === id
      && (item.result === 'success' || item.result === 'failure')
      && (filter === 'all' || item.result === filter)
      && Object.hasOwn(outcomeLabels, item.outcome)
      && Number.isInteger(item.http_status) && item.http_status >= 100 && item.http_status <= 599);
  if (!valid) throw new Error(loadError);
  return result;
}

function useReceipts(id: string) {
  const [data, setData] = useState<ConnectorReceiptPage | null>(null);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<ReceiptFilter>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const sequence = useRef(0);
  const load = useCallback(async (nextPage: number, nextFilter: ReceiptFilter): Promise<void> => {
    const current = ++sequence.current;
    setPage(nextPage); setFilter(nextFilter);
    setLoading(true); setData(null); setError(false);
    try {
      let result = await requestPage(id, nextPage, nextFilter);
      if (current !== sequence.current) return;
      if (nextPage > 1 && result.offset >= result.total) {
        nextPage = Math.max(1, Math.ceil(result.total / pageSize));
        result = await requestPage(id, nextPage, nextFilter);
        if (current !== sequence.current) return;
        if (nextPage > 1 && result.offset >= result.total) throw new Error(loadError);
      }
      setPage(nextPage); setData(result);
    } catch {
      if (current === sequence.current) setError(true);
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [id]);
  useEffect(() => {
    void load(1, 'all');
    return () => { sequence.current += 1; };
  }, [load]);
  return { data, page, filter, loading, error, load };
}

export function AlertConnectorReceiptsModal({ target, enabled, onClose }: Props) {
  return enabled && target ? <ReceiptContext key={target.id} target={target} onClose={onClose} /> : null;
}

function ReceiptContext({ target, onClose }: Pick<Props, 'onClose'> & { target: AlertConnector }) {
  const state = useReceipts(target.id);
  return <Modal title={`接收记录：${target.name}`} open footer={null}
    onCancel={onClose} width={840} destroyOnHidden>
    <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: 16 }}>
      <Segmented<ReceiptFilter> aria-label="接收结果筛选" value={state.filter}
        options={[{ label: '全部', value: 'all' }, { label: '成功', value: 'success' }, { label: '失败', value: 'failure' }]}
        onChange={(value) => void state.load(1, value)} />
      <Button aria-label="刷新接收记录" icon={<RefreshCw size={16} />}
        loading={state.loading} onClick={() => void state.load(state.page, state.filter)} />
    </div>
    {state.error ? <Alert type="error" showIcon message={loadError} /> : null}
    {state.loading ? <div role="status" style={{ padding: 28, textAlign: 'center' }}>
      <Spin size="small" /><span style={{ marginLeft: 8 }}>正在加载接收记录</span>
    </div> : null}
    {!state.loading && !state.error && state.data ? <>
      <Table<ConnectorReceipt> rowKey="id" dataSource={state.data.items} pagination={false}
        scroll={{ x: 620 }} locale={{ emptyText: '暂无接收记录' }} columns={[
          { title: '接收时间', dataIndex: 'received_at', render: formatTime },
          { title: '结果', dataIndex: 'result', render: (value: ConnectorReceiptResult) => <Tag color={value === 'success' ? 'green' : 'red'}>{value === 'success' ? '成功' : '失败'}</Tag> },
          { title: '响应状态', dataIndex: 'http_status', render: (value: number) => `HTTP ${value}` },
          { title: '处理结果', dataIndex: 'outcome', render: (value: ConnectorReceipt['outcome'], item) => <div>
            {outcomeLabels[value]}
            {item.result === 'failure' ? <div>{getReceiptFailureMessage(item.error_code)}</div> : null}
          </div> },
        ]} />
      <nav aria-label="接收记录分页" style={{ marginTop: 16 }}>
        <Space wrap>
          <Button aria-label="上一页接收记录" icon={<ChevronLeft size={16} />} disabled={state.page <= 1}
            onClick={() => void state.load(state.page - 1, state.filter)} />
          <span>第 {state.page} / {Math.max(1, Math.ceil(state.data.total / pageSize))} 页 · 共 {state.data.total} 条</span>
          <Button aria-label="下一页接收记录" icon={<ChevronRight size={16} />}
            disabled={state.page * pageSize >= state.data.total}
            onClick={() => void state.load(state.page + 1, state.filter)} />
        </Space>
      </nav>
    </> : null}
  </Modal>;
}
