import { useState } from 'react';
import { Button, Tabs, Tag } from 'antd';
import { ArrowLeft } from 'lucide-react';
import type { AuthUser } from '../api/auth';
import type { ExternalAlert } from '../api/externalAlerts';
import { ActiveEventsTab } from '../components/events/ActiveEventsTab';
import { AlertConnectorsTab } from '../components/events/AlertConnectorsTab';
import { ExternalAlertsTab } from '../components/events/ExternalAlertsTab';
import { AlertNotificationsTab } from '../components/events/AlertNotificationsTab';
import { DiagnosticGrantsTab } from '../components/events/DiagnosticGrantsTab';

type EventsPageProps = {
  currentUser: AuthUser;
  onOpenTerminal?: (assetId: string) => void;
  onOpenIncidents?: () => void;
  initialAssetId?: string;
  initialAlertId?: string;
  initialTab?: 'activity' | 'external';
};
type ConfigurationTab = 'notifications' | 'diagnostic-grants';

export function EventsPage(props: EventsPageProps) {
  return <EventsContent key={JSON.stringify([props.currentUser.id, props.currentUser.role,
    props.initialAssetId, props.initialAlertId, props.initialTab])} {...props} />;
}

function EventsContent({ currentUser, onOpenTerminal, onOpenIncidents, initialAssetId, initialAlertId, initialTab = 'activity' }: EventsPageProps) {
  const [activeTab, setActiveTab] = useState<string>(initialTab);
  const [configurationAlert, setConfigurationAlert] = useState<Pick<ExternalAlert, 'id' | 'asset_id'> | null>(null);
  const openConfiguration = (tab: ConfigurationTab, alert: ExternalAlert) => {
    if (currentUser.role !== 'admin') return;
    setConfigurationAlert({ id: alert.id, asset_id: alert.asset_id });
    setActiveTab(tab);
  };
  const assetId = configurationAlert?.asset_id ?? initialAssetId;
  const alertId = configurationAlert?.id ?? initialAlertId;
  return <section className="events-page">
    <div className="page-heading">
      <div><p className="eyebrow">统一接入 · 人工研判 · 处置留痕</p><h1>告警与智能预警</h1></div>
      <Tag color="blue">外部告警只读接入</Tag>
    </div>
    {configurationAlert && ['notifications', 'diagnostic-grants'].includes(activeTab)
      ? <Button icon={<ArrowLeft size={16} />} onClick={() => setActiveTab('external')}>返回告警</Button> : null}
    <Tabs
      className="events-tabs"
      activeKey={activeTab}
      onChange={setActiveTab}
      destroyOnHidden
      items={[
        { key: 'activity', label: '活动告警', children: <ActiveEventsTab currentUser={currentUser} initialAssetId={initialAssetId} /> },
        { key: 'external', label: '外部告警研判', children: <ExternalAlertsTab currentUser={currentUser} initialAssetId={assetId} initialAlertId={alertId} onOpenTerminal={onOpenTerminal} onOpenIncidents={onOpenIncidents} onOpenConfiguration={openConfiguration} /> },
        { key: 'connectors', label: '接入连接器', children: <AlertConnectorsTab currentUser={currentUser} /> },
        { key: 'notifications', label: '管理员通知', children: <AlertNotificationsTab currentUser={currentUser} /> },
        { key: 'diagnostic-grants', label: '只读诊断授权', children: <DiagnosticGrantsTab key={assetId} currentUser={currentUser} initialAssetId={assetId} /> },
      ]}
    />
  </section>;
}
