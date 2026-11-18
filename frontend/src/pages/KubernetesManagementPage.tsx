import { useState } from 'react';
import { Tabs } from 'antd';
import type { AuthUser } from '../api/auth';
import type { KubernetesCluster } from '../api/kubernetes';
import { KubernetesClustersPage } from './KubernetesClustersPage';
import { KubernetesChangesPage } from './KubernetesChangesPage';
import { KubernetesOverviewPage } from './KubernetesOverviewPage';

export function KubernetesManagementPage({ currentUser }: { currentUser: Pick<AuthUser, 'role'> }) {
  const [tab, setTab] = useState('connections');
  const [selected, setSelected] = useState<KubernetesCluster>();
  return <div className="kubernetes-management">
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'connections', label: '集群接入' }, { key: 'overview', label: '集群总览' },
      ...(currentUser.role === 'admin' ? [{ key: 'changes', label: '集群变更' }] : []),
    ]} />
    {tab === 'connections' ? <KubernetesClustersPage currentUser={currentUser} onOpenOverview={(cluster) => {
      setSelected(cluster); setTab('overview');
    }} /> : tab === 'changes' && currentUser.role === 'admin' ? <KubernetesChangesPage initialCluster={selected} />
    : <KubernetesOverviewPage currentUser={currentUser} initialCluster={selected}
      onOpenConnections={() => setTab('connections')} />}
  </div>;
}
