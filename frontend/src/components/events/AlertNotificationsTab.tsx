import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Spin } from 'antd';
import { RefreshCw } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import { getAlertNotificationConfig, type AlertNotificationConfig } from '../../api/alertNotifications';
import { AlertNotificationConfigForm } from './AlertNotificationConfigForm';
import { AlertNotificationHistory } from './AlertNotificationHistory';
import './AlertNotifications.css';

export function AlertNotificationsTab({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [config, setConfig] = useState<AlertNotificationConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [historyRevision, setHistoryRevision] = useState(0);
  const generation = useRef(0);
  const load = async () => {
    const requestId = ++generation.current;
    setLoading(true); setError('');
    try {
      const result = await getAlertNotificationConfig();
      if (requestId === generation.current) setConfig(result);
    } catch (failure) {
      if (requestId === generation.current) setError(failure instanceof Error ? failure.message : '通知配置加载失败');
    } finally { if (requestId === generation.current) setLoading(false); }
  };
  useEffect(() => { void load(); return () => { generation.current += 1; }; }, []);
  return <div className="alert-notifications-tab">
    {loading ? <div className="notification-config-loading"><Spin size="small" /></div> : error
      ? <Alert type="error" showIcon message={error} action={<Button icon={<RefreshCw size={15} />} onClick={() => void load()}>重新加载</Button>} />
      : config ? <AlertNotificationConfigForm config={config} writable={currentUser.role === 'admin'}
        onSaved={(next) => { setConfig(next); setHistoryRevision((value) => value + 1); }} /> : null}
    <AlertNotificationHistory currentUser={currentUser} refreshVersion={historyRevision} />
  </div>;
}
