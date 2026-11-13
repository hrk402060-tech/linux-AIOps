import { Tabs, Tag } from 'antd';
import type { AuthUser } from '../api/auth';
import { ActiveEventsTab } from '../components/events/ActiveEventsTab';
import { AlertConnectorsTab } from '../components/events/AlertConnectorsTab';
import { ExternalAlertsTab } from '../components/events/ExternalAlertsTab';
import { AlertNotificationsTab } from '../components/events/AlertNotificationsTab';
import { DiagnosticGrantsTab } from '../components/events/DiagnosticGrantsTab';

export function EventsPage({ currentUser, onOpenTerminal, onOpenIncidents, initialAssetId, initialAlertId, initialTab = 'activity' }: {
  currentUser: AuthUser;
  onOpenTerminal?: (assetId: string) => void;
  onOpenIncidents?: () => void;
  initialAssetId?: string;
  initialAlertId?: string;
  initialTab?: 'activity' | 'external';
}) {
  return <section className="events-page">
    <div className="page-heading">
      <div><p className="eyebrow">统一接入 · 人工研判 · 处置留痕</p><h1>告警与智能预警</h1></div>
      <Tag color="blue">外部告警只读接入</Tag>
    </div>
    <Tabs
      key={JSON.stringify([initialAssetId, initialAlertId, initialTab])}
      className="events-tabs"
      defaultActiveKey={initialTab}
      destroyOnHidden
      items={[
        { key: 'activity', label: '活动告警', children: <ActiveEventsTab currentUser={currentUser} initialAssetId={initialAssetId} /> },
        { key: 'external', label: '外部告警研判', children: <ExternalAlertsTab currentUser={currentUser} initialAssetId={initialAssetId} initialAlertId={initialAlertId} onOpenTerminal={onOpenTerminal} onOpenIncidents={onOpenIncidents} /> },
        { key: 'connectors', label: '接入连接器', children: <AlertConnectorsTab currentUser={currentUser} /> },
        { key: 'notifications', label: '管理员通知', children: <AlertNotificationsTab currentUser={currentUser} /> },
        { key: 'diagnostic-grants', label: '只读诊断授权', children: <DiagnosticGrantsTab currentUser={currentUser} /> },
      ]}
    />
  </section>;
}
