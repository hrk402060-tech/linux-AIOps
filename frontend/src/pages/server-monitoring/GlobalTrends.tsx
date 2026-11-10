import { useCallback } from 'react';
import { Alert, Skeleton } from 'antd';

import { getFleetTrends } from '../../api/monitoring';
import { useMonitoringRequest } from './useMonitoringRequest';
import { TrendChart } from './TrendChart';
import type { FleetAsset } from '../../types/monitoring';
import { metric } from './values';

export function GlobalTrends({ assets, hours, revision }: { assets: FleetAsset[]; hours: number; revision: number }) {
  const idsKey = assets.map((asset) => asset.asset_id).sort().join(',');
  const eligibleKey = assets.filter((asset) => metric(asset, 'cpu_percent') !== null
    || metric(asset, 'memory_percent') !== null).map((asset) => asset.asset_id).sort().join(',');
  const request = useCallback((signal: AbortSignal) => getFleetTrends(idsKey ? idsKey.split(',') : [], hours, signal), [idsKey, hours]);
  const result = useMonitoringRequest(`${idsKey}:${eligibleKey}:${hours}:${revision}`, request);
  const cpuCount = assets.filter((asset) => metric(asset, 'cpu_percent') !== null).length;
  const memoryCount = assets.filter((asset) => metric(asset, 'memory_percent') !== null).length;
  return <section className="fleet-section fleet-global-trends" aria-label="全局资源趋势">
    <div className="fleet-section-heading"><div><h2>全局资源趋势</h2><p>当前筛选范围 · {assets.length} 台服务器 · 最近 {hours} 小时</p></div>
      <span className="fleet-neutral-badge">CPU {cpuCount} 台有效 / 内存 {memoryCount} 台有效</span></div>
    <p className="fleet-method">CPU、内存分别按有效服务器等权平均；每台服务器每个时间桶取最后有效值，离线、过期及缺失值不补 0。悬浮查看各时间桶实际参与台数。</p>
    {result.loading ? <Skeleton active paragraph={{ rows: 4 }} title={false} /> : result.error
      ? <Alert type="warning" showIcon message={`资源趋势不可用：${result.error}`} />
      : <TrendChart title="当前筛选范围 CPU 和内存趋势" unit="%" maximum={100}
        points={(result.data?.buckets ?? []).map((point) => ({ time: point.collected_at,
          values: { cpu: point.cpu_percent, memory: point.memory_percent },
          note: `CPU ${point.cpu_asset_count} 台 / 内存 ${point.memory_asset_count} 台` }))}
        series={[{ key: 'cpu', label: 'CPU 平均使用率', color: '#0d9488' }, { key: 'memory', label: '内存平均使用率', color: '#7c3aed', dash: '7 4' }]} />}
  </section>;
}
