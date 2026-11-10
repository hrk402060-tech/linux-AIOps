import { ChevronDown, Cpu, MemoryStick, HardDrive, Activity, Server, Bell, ShieldAlert } from 'lucide-react';

import type { FleetAsset, ResourceKey, ResourceState } from '../../types/monitoring';
import { connection, connectionLabels, coreCount, displayState, metric, percent, stateLabels, timestamp, tone } from './values';

export function MetricBar({ name, value, state, suffix = '%', stateText }: {
  name: string; value: number | null; state: ResourceState; suffix?: string; stateText?: string;
}) {
  const width = value === null ? 0 : Math.min(100, Math.max(0, value));
  return <div className={`fleet-metric is-${tone(state)}`}>
    <div><span>{name}</span><strong>{value === null ? '—' : `${value.toFixed(1)}${suffix}`}</strong></div>
    <div className="fleet-meter" aria-hidden="true">{value !== null ? <i style={{ width: `${width}%` }} /> : null}</div>
    <small>{stateText ?? stateLabels[state]}</small>
  </div>;
}
export function ResourceBadge({ state }: { state: ResourceState }) {
  return <span className={`fleet-resource-badge is-${tone(state)}`}>{stateLabels[state]}</span>;
}
export function ServerCard({ asset, expanded, onToggle }: { asset: FleetAsset; expanded: boolean; onToggle: () => void }) {
  const status = connection(asset);
  const cores = coreCount(asset);
  const load = metric(asset, 'load_1m');
  const loadState = displayState(asset, 'load');
  const metrics: { key: ResourceKey; field: string; name: string; icon: typeof Cpu }[] = [
    { key: 'cpu', field: 'cpu_percent', name: 'CPU', icon: Cpu },
    { key: 'memory', field: 'memory_percent', name: '内存', icon: MemoryStick },
    { key: 'disk', field: 'disk_percent', name: '磁盘', icon: HardDrive },
  ];
  return <article className={`fleet-card${expanded ? ' is-selected' : ''}`} data-testid={`server-${asset.asset_id}`}>
    <button className="fleet-card-button" aria-expanded={expanded} aria-controls={`monitor-detail-${asset.asset_id}`}
      aria-label={`${expanded ? '收起' : '查看'} ${asset.asset_name} 监控详情`} onClick={onToggle}>
      <div className="fleet-card-heading"><span className="fleet-server-icon"><Server size={22} /></span>
        <div className="fleet-card-title"><strong>{asset.asset_name}</strong><small>{asset.host} <span>· {asset.group_name || '未分组'}</span></small></div>
        <span className={`fleet-connection is-${status}`}><i />{connectionLabels[status]}</span>
        <ChevronDown className="fleet-expand-icon" size={18} />
      </div>
      <div className="fleet-card-resources">{metrics.map(({ key, field, name, icon: Icon }) => <div key={key}>
        <div className="fleet-metric-caption"><Icon size={14} />{name}</div>
        <MetricBar name="使用率" value={metric(asset, field)} state={displayState(asset, key)} />
      </div>)}</div>
      <div className="fleet-card-load"><span><Activity size={14} />系统负载 <b>{load === null ? '—' : load.toFixed(2)}</b></span>
        <small>{cores === null ? '核数未知' : `${cores} 逻辑核`}</small><ResourceBadge state={loadState} /></div>
      <div className="fleet-card-foot">
        <span className={asset.active_alerts ? 'is-danger-text' : ''}><Bell size={14} />告警 <b>{asset.active_alerts}</b></span>
        <span className={asset.pending_vulnerabilities ? 'is-warning-text' : ''}><ShieldAlert size={14} />漏洞 <b>{asset.pending_vulnerabilities}</b></span>
        <time dateTime={asset.latest_snapshot?.collected_at}>采集 {timestamp(asset.latest_snapshot?.collected_at)}</time>
      </div>
    </button>
  </article>;
}
export function CurrentValue({ label, value, state }: { label: string; value: number | null; state: ResourceState }) {
  return <div className={`fleet-current-value is-${tone(state)}`}><span>{label}</span><strong>{percent(value)}</strong><ResourceBadge state={state} /></div>;
}
