import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Tag, Typography, type TableColumnsType } from 'antd';
import { List } from 'lucide-react';
import {
  getAlertConnectorRuntime, type AlertConnector, type ConnectorRuntime,
} from '../../api/externalAlerts';
import { AlertConnectorReceiptsModal, getReceiptFailureMessage } from './AlertConnectorReceiptsModal';

const healthLabels: Record<ConnectorRuntime['health'], string> = {
  disabled: '已停用', unverified: '尚无接收记录',
  healthy: '最近接收成功', error: '最近接收失败',
};
const healthColors: Record<ConnectorRuntime['health'], string> = {
  disabled: 'default', unverified: 'default', healthy: 'green', error: 'red',
};

function validRuntime(items: ConnectorRuntime[]): boolean {
  return Array.isArray(items) && items.every((item) => typeof item.connector_id === 'string'
    && Object.hasOwn(healthLabels, item.health)
    && Number.isSafeInteger(item.success_count) && item.success_count >= 0
    && Number.isSafeInteger(item.failure_count) && item.failure_count >= 0);
}

function useRuntimeSummary(enabled: boolean) {
  const [items, setItems] = useState<ConnectorRuntime[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState(false);
  const allowed = useRef(enabled);
  const sequence = useRef(0);
  allowed.current = enabled;
  const refresh = useCallback(async (): Promise<void> => {
    if (!allowed.current) return;
    const current = ++sequence.current;
    setItems([]); setLoading(true); setError(false);
    try {
      const result = await getAlertConnectorRuntime();
      if (!validRuntime(result)) throw new Error('Invalid connector runtime');
      if (current === sequence.current && allowed.current) setItems(result);
    } catch {
      if (current === sequence.current && allowed.current) setError(true);
    } finally {
      if (current === sequence.current && allowed.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (enabled) void refresh();
    else { setItems([]); setLoading(false); setError(false); }
    return () => { sequence.current += 1; };
  }, [enabled, refresh]);
  return { items, loading, error, refresh };
}

export function useAlertConnectorRuntime(enabled: boolean) {
  const summary = useRuntimeSummary(enabled);
  const [target, setTarget] = useState<AlertConnector | null>(null);
  useEffect(() => { if (!enabled) setTarget(null); }, [enabled]);
  const renderValue = (connector: AlertConnector, render: (item: ConnectorRuntime) => ReactNode): ReactNode => {
    if (summary.loading) return <Typography.Text type="secondary">运行数据加载中</Typography.Text>;
    if (summary.error) return <Typography.Text type="secondary">暂不可用</Typography.Text>;
    const item = summary.items.find((entry) => entry.connector_id === connector.id);
    return item ? render(item) : <Typography.Text type="secondary">暂无运行数据</Typography.Text>;
  };
  const columns: TableColumnsType<AlertConnector> = enabled ? [
    { title: '运行状态', key: 'runtime_health', render: (_, connector) => renderValue(connector,
      (item) => <div><Tag color={healthColors[item.health]}>{healthLabels[item.health]}</Tag>
        {item.health === 'error' ? <div><Typography.Text type="secondary">
          {getReceiptFailureMessage(item.last_error_code)}
        </Typography.Text></div> : null}
      </div>) },
    { title: '最近接收', key: 'last_received_at', render: (_, connector) => renderValue(connector,
      (item) => item.last_received_at ? new Date(item.last_received_at).toLocaleString('zh-CN') : '-') },
    { title: '接收次数', key: 'receipt_counts', render: (_, connector) => renderValue(connector,
      (item) => <span style={{ whiteSpace: 'nowrap' }}>成功 {item.success_count} / 失败 {item.failure_count}</span>) },
    { title: '接收记录', key: 'receipts', render: (_, connector) => <Button
      aria-label={`接收记录 ${connector.name}`} icon={<List size={15} />}
      onClick={() => setTarget(connector)}>接收记录</Button> },
  ] : [];
  const notice = enabled ? <>
    <Typography.Paragraph type="secondary" style={{ margin: '0 0 12px' }}>
      仅统计本功能启用后的接收记录，历史不回填；最近接收成功不代表诊断或通知已验证。
    </Typography.Paragraph>
    {summary.error ? <Alert type="error" showIcon message="运行数据加载失败，请刷新后重试"
      style={{ marginBottom: 12 }} /> : null}
  </> : null;
  const modal = <AlertConnectorReceiptsModal target={target} enabled={enabled} onClose={() => setTarget(null)} />;
  return { columns, notice, modal, refresh: summary.refresh };
}
