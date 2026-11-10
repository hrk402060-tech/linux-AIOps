import { Empty } from 'antd';
import { HardDrive, Network } from 'lucide-react';

import type { FleetAsset, MonitorSnapshot, ResourceState } from '../../types/monitoring';
import { TrendChart, type ChartPoint } from './TrendChart';
import { MetricBar } from './ServerCard';
import { bytes, connection, interfaces, numeric, partitions, percent, rate } from './values';

export function NetworkDetails({ asset, history, gapSeconds }: { asset: FleetAsset; history: MonitorSnapshot[]; gapSeconds: number }) {
  const online = connection(asset) === 'online';
  const latest = interfaces(asset.latest_snapshot?.metrics ?? {});
  const names = [...new Set([...latest.map((item) => item.name),
    ...history.flatMap((snapshot) => interfaces(snapshot.metrics).map((item) => item.name))])].sort();
  return <section className="fleet-detail-section"><div className="fleet-section-heading"><h3><Network size={17} />网卡流量</h3><small>逐网卡双样本差值 · KiB/s = 1024 B/s</small></div>
    {!names.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无逐网卡采样，请重新采集" /> : <div className="fleet-network-grid">{names.map((name) => {
      const current = latest.find((item) => item.name === name);
      const points: ChartPoint[] = history.map((snapshot) => {
        const row = snapshot.status === 'online' ? interfaces(snapshot.metrics).find((item) => item.name === name) : null;
        return { time: snapshot.collected_at, values: { rx: row?.rx_bytes_per_second == null ? null : row.rx_bytes_per_second / 1024,
          tx: row?.tx_bytes_per_second == null ? null : row.tx_bytes_per_second / 1024 } };
      });
      return <div className="fleet-network-item" key={name}><div className="fleet-network-heading"><strong>{name}</strong>
        <span>入站 <b>{rate(online ? current?.rx_bytes_per_second ?? null : null)}</b> / 出站 <b>{rate(online ? current?.tx_bytes_per_second ?? null : null)}</b></span></div>
        <TrendChart title={`${asset.asset_name} ${name} 入站与出站趋势`} unit="KiB/s" points={points} gapSeconds={gapSeconds}
          series={[{ key: 'rx', label: '入站', color: '#0d9488' }, { key: 'tx', label: '出站', color: '#7c3aed', dash: '7 4' }]} />
      </div>;
    })}</div>}
  </section>;
}
export function PartitionDetails({ asset }: { asset: FleetAsset }) {
  const available = connection(asset) === 'online';
  const rows = partitions(asset.latest_snapshot?.metrics ?? {});
  const thresholds = asset.policy.severity_rules.disk;
  const severity = (value: number | null): ResourceState => value === null ? 'unknown'
    : value >= thresholds.critical ? 'pending_critical' : value >= thresholds.warning ? 'pending_warning' : 'normal';
  return <section className="fleet-detail-section"><div className="fleet-section-heading"><h3><HardDrive size={17} />实际分区</h3>
    <small>分区当前值 · 关注 ≥{thresholds.warning}% / 超限 ≥{thresholds.critical}%</small></div>
    {!rows.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无实际分区数据，请重新采集" /> : <div className="fleet-table-scroll">
      <table className="fleet-partitions"><thead><tr><th>挂载点 / 设备</th><th>总容量</th><th>已用</th><th>可用</th><th>使用率</th><th>inode 使用率</th></tr></thead>
        <tbody>{rows.map((disk, index) => {
          const usage = available ? numeric(disk.used_percent, 100) : null;
          return <tr key={`${disk.device}:${disk.mount}:${index}`}><td><strong>{disk.mount}</strong><small>{disk.device || '设备信息未采集'}</small></td>
            <td>{bytes(available ? disk.total_bytes : null)}</td><td>{bytes(available ? disk.used_bytes : null)}</td><td>{bytes(available ? disk.available_bytes : null)}</td>
            <td><MetricBar name="当前值" value={usage} state={severity(usage)}
              stateText={usage === null ? '未知' : usage >= thresholds.critical ? '当前值超限' : usage >= thresholds.warning ? '当前值关注' : '当前值正常'} /></td><td>{percent(available ? disk.inode_percent : null)}</td>
          </tr>;
        })}</tbody></table>
    </div>}
  </section>;
}
