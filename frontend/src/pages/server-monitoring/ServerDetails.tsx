import { useCallback, useState } from 'react';
import { Alert, Button, Skeleton } from 'antd';
import { RefreshCw, Settings2, X } from 'lucide-react';

import type { FleetAsset, MonitorSnapshot } from '../../types/monitoring';
import { collectMonitor, getMonitorHistory } from '../../api/monitoring';
import { useMonitoringRequest } from './useMonitoringRequest';
import { useMonitoringAction } from './useMonitoringAction';
import { TrendChart } from './TrendChart';
import { CurrentValue, ResourceBadge } from './ServerCard';
import { NetworkDetails, PartitionDetails } from './DetailResources';
import { RulesModal } from './RulesModal';
import { connection, connectionLabels, coreCount, displayState, metric, numeric, timestamp } from './values';

export function ServerDetails({ asset, hours, revision, gapSeconds, canManage, onClose, onRefresh }: {
  asset: FleetAsset; hours: number; revision: number; gapSeconds: number; canManage: boolean; onClose: () => void; onRefresh: () => void;
}) {
  const request = useCallback((signal: AbortSignal) => getMonitorHistory(asset.asset_id, hours, signal), [asset.asset_id, hours]);
  const result = useMonitoringRequest(`${asset.asset_id}:${hours}:${revision}`, request);
  const [rulesOpen, setRulesOpen] = useState(false);
  const collection = useMonitoringAction();
  const history = (result.data ?? []).filter((item) => item.asset_id === asset.asset_id)
    .sort((a, b) => Date.parse(a.collected_at) - Date.parse(b.collected_at));
  const cores = coreCount(asset);
  const load = metric(asset, 'load_1m');
  const rule = asset.policy.severity_rules;
  const thresholds = cores === null ? [] : [
    { value: rule.load.warning * cores, label: '关注阈值', color: '#b77909' },
    { value: rule.load.critical * cores, label: '超限阈值', color: '#dc3545' },
  ];
  const sample = (snapshot: MonitorSnapshot, key: string) => snapshot.status === 'online'
    ? numeric(snapshot.metrics[key], key.endsWith('_percent') ? 100 : Infinity) : null;
  const collect = () => collection.run(() => collectMonitor(asset.asset_id), onRefresh);
  return <section id={`monitor-detail-${asset.asset_id}`} className="fleet-details" aria-label={`${asset.asset_name} 监控详情`}>
    <div className="fleet-detail-heading"><div><span className="fleet-eyebrow">服务器详细监控</span><h2>{asset.asset_name} <small>{asset.host}</small></h2>
      <p>采集于 {timestamp(asset.latest_snapshot?.collected_at)} <span className={`fleet-connection is-${connection(asset)}`}><i />{connectionLabels[connection(asset)]}</span></p></div>
      <div className="fleet-detail-actions">
        {canManage ? <><Button icon={<Settings2 size={15} />} onClick={() => setRulesOpen(true)}>资源规则</Button>
          <Button icon={<RefreshCw size={15} />} loading={collection.busy} disabled={asset.lifecycle_status !== 'enabled'} onClick={() => void collect()}>重新采集</Button></> : null}
        <Button type="text" aria-label={`收起 ${asset.asset_name} 详情`} icon={<X size={18} />} onClick={onClose} />
      </div>
    </div>
    {connection(asset) !== 'online' ? <Alert type="warning" showIcon message={`${connectionLabels[connection(asset)]}，当前指标不可用`}
      description={asset.latest_snapshot?.error_message || '当前资源状态不作为正常判断；下方趋势仅展示可用历史样本。'} /> : null}
    {collection.error ? <Alert type="error" showIcon message={collection.error} /> : null}
    <div className="fleet-current-grid">
      <CurrentValue label="CPU 使用率" value={metric(asset, 'cpu_percent')} state={displayState(asset, 'cpu')} />
      <CurrentValue label="内存使用率" value={metric(asset, 'memory_percent')} state={displayState(asset, 'memory')} />
      <CurrentValue label="最高分区使用率" value={metric(asset, 'disk_percent')} state={displayState(asset, 'disk')} />
      <div className={`fleet-current-value is-${displayState(asset, 'load')}`}><span>1 分钟负载</span>
        <strong>{load === null ? '—' : load.toFixed(2)}</strong><small>{cores === null ? '核数未知' : `${cores} 逻辑 CPU 核`}</small><ResourceBadge state={displayState(asset, 'load')} /></div>
    </div>
    <p className="fleet-method">资源状态按连续 {rule.duration_minutes} 分钟有效采样确认；数据缺失或采集间断会中断计时。
      {cores === null ? ' 核数未知，负载不判定正常或异常。' : ` 负载关注 ≥ ${cores} × ${rule.load.warning} = ${(cores * rule.load.warning).toFixed(1)}，超限 ≥ ${cores} × ${rule.load.critical} = ${(cores * rule.load.critical).toFixed(1)}。`}</p>
    {result.loading ? <Skeleton active paragraph={{ rows: 6 }} title={false} /> : result.error
      ? <Alert type="error" showIcon message="服务器历史读取失败" description={result.error} action={<Button onClick={onRefresh}>重试</Button>} />
      : <>
        {history.length >= 500 ? <Alert type="info" showIcon message="当前仅展示最近 500 条真实样本，可缩短时间范围查看更多细节。" /> : null}
        <div className="fleet-detail-chart-grid">
          <section className="fleet-detail-section"><h3>CPU 与内存</h3>
            <TrendChart title={`${asset.asset_name} CPU 与内存趋势`} unit="%" gapSeconds={gapSeconds}
              points={history.map((row) => ({ time: row.collected_at, values: { cpu: sample(row, 'cpu_percent'), memory: sample(row, 'memory_percent') } }))}
              series={[{ key: 'cpu', label: 'CPU', color: '#0d9488' }, { key: 'memory', label: '内存', color: '#7c3aed', dash: '7 4' }]}
              thresholds={[
                { value: rule.cpu.warning, label: rule.memory.warning === rule.cpu.warning ? 'CPU/内存关注' : 'CPU 关注', color: '#b77909' },
                { value: rule.cpu.critical, label: rule.memory.critical === rule.cpu.critical ? 'CPU/内存超限' : 'CPU 超限', color: '#dc3545' },
                ...(rule.memory.warning === rule.cpu.warning ? [] : [{ value: rule.memory.warning, label: '内存关注', color: '#b77909' }]),
                ...(rule.memory.critical === rule.cpu.critical ? [] : [{ value: rule.memory.critical, label: '内存超限', color: '#dc3545' }]),
              ]} maximum={100} />
          </section>
          <section className="fleet-detail-section"><h3>系统负载 <small>{cores === null ? '核数未知' : `${cores} 逻辑核`}</small></h3>
            <TrendChart title={`${asset.asset_name} 1、5、15 分钟负载趋势`} unit="负载" gapSeconds={gapSeconds}
              points={history.map((row) => ({ time: row.collected_at, values: { one: sample(row, 'load_1m'), five: sample(row, 'load_5m'), fifteen: sample(row, 'load_15m') } }))}
              series={[{ key: 'one', label: '1 分钟', color: '#0d9488' }, { key: 'five', label: '5 分钟', color: '#7c3aed', dash: '7 4' },
                { key: 'fifteen', label: '15 分钟', color: '#c39316', dash: '2 5' }]} thresholds={thresholds} />
          </section>
        </div>
        <NetworkDetails asset={asset} history={history} gapSeconds={gapSeconds} />
      </>}
    <PartitionDetails asset={asset} />
    {rulesOpen ? <RulesModal asset={asset} onClose={() => setRulesOpen(false)} onSaved={onRefresh} /> : null}
  </section>;
}
