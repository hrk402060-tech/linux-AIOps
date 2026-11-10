import { Activity, Bell, Server, ShieldAlert, TriangleAlert } from 'lucide-react';

import type { FleetAsset } from '../../types/monitoring';
import { connection, connectionLabels, displayState } from './values';

export function FleetSummary({ assets }: { assets: FleetAsset[] }) {
  const online = assets.filter((a) => connection(a) === 'online').length;
  const attention = assets.filter((a) => connection(a) !== 'disabled' && (connection(a) !== 'online'
    || Object.keys(a.resource_statuses).some((key) => {
      const state = displayState(a, key as keyof typeof a.resource_statuses);
      return state !== 'normal' && state !== 'unknown';
    }) || a.active_alerts > 0)).length;
  const values = [
    { name: '范围内服务器', count: assets.length, icon: Server, tone: 'neutral' },
    { name: '在线服务器', count: online, icon: Activity, tone: 'normal' },
    { name: '需要关注', count: attention, icon: TriangleAlert, tone: attention ? 'warning' : 'normal' },
    { name: '活动告警', count: assets.reduce((sum, a) => sum + a.active_alerts, 0), icon: Bell, tone: 'critical' },
    { name: '待处理漏洞', count: assets.reduce((sum, a) => sum + a.pending_vulnerabilities, 0), icon: ShieldAlert, tone: 'warning' },
  ];
  const statuses = Object.keys(connectionLabels).map((key) => ({ key,
    count: assets.filter((a) => connection(a) === key).length })).filter((item) => item.count > 0);
  return <>
    <div className="fleet-kpis">{values.map(({ name, count, icon: Icon, tone }) => <div className={`fleet-kpi is-${tone}`} key={name}>
      <span><Icon size={19} /></span><div><small>{name}</small><strong>{count}</strong></div>
    </div>)}</div>
    <div className="fleet-distribution" aria-label="服务器状态分布"><strong>连接与采集</strong>
      <div className="fleet-distribution-bar">{statuses.map(({ key, count }) => <i key={key} className={`is-${key}`} style={{ flex: count }} title={`${connectionLabels[key]} ${count}`} />)}</div>
      <div className="fleet-distribution-labels">{statuses.map(({ key, count }) => <span key={key}><i className={`is-${key}`} />{connectionLabels[key]} {count}</span>)}</div>
    </div>
  </>;
}
