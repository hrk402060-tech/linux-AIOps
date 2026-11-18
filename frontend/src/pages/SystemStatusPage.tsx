import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { RefreshCw } from 'lucide-react';
import { getSystemStatus, type SystemStatus } from '../api/system';
import { PlatformStatus } from './PlatformStatus';

export function SystemStatusPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const load = async () => {
    const sequence = ++generation.current;
    setLoading(true); setError('');
    try {
      const next = await getSystemStatus();
      if (sequence === generation.current) setStatus(next);
    } catch (cause) {
      if (sequence === generation.current) setError(cause instanceof Error ? cause.message : '平台状态加载失败');
    } finally { if (sequence === generation.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, []);
  return <section className="system-status-page">
    <div className="page-heading"><h1>平台状态</h1>
      <Button aria-label="刷新平台状态" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()}>刷新</Button>
    </div>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    <Spin spinning={loading}>{status ? <PlatformStatus status={status} /> : null}</Spin>
  </section>;
}
