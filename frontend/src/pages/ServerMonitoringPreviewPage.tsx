import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Alert, Button, Empty, Input, Select, Skeleton } from 'antd';
import { RefreshCw, Search } from 'lucide-react';

import { getFleet } from '../api/monitoring';
import { FleetSummary } from './server-monitoring/FleetSummary';
import { ServerCard } from './server-monitoring/ServerCard';
import { ServerDetails } from './server-monitoring/ServerDetails';
import { GlobalTrends } from './server-monitoring/GlobalTrends';
import { useMonitoringRequest } from './server-monitoring/useMonitoringRequest';
import { useFleetFreshness } from './server-monitoring/useFleetFreshness';
import { connection, connectionLabels, timestamp } from './server-monitoring/values';
import './server-monitoring/fleet.css';

export function ServerMonitoringPreviewPage({ canManage = false }: { canManage?: boolean }) {
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState<string>();
  const [status, setStatus] = useState<string>();
  const [hours, setHours] = useState(24);
  const [revision, setRevision] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  const request = useCallback((signal: AbortSignal) => getFleet(signal), []);
  const result = useMonitoringRequest(`fleet:${revision}`, request);
  const fleet = useFleetFreshness(result.data);
  const assets = useMemo(() => (fleet?.assets ?? []).filter((asset) => {
    const match = `${asset.asset_name} ${asset.host}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
    return match && (!group || (asset.group_name || '未分组') === group) && (!status || connection(asset) === status);
  }).sort((a, b) => {
    const priority = (value: typeof a) => (connection(value) === 'offline' || connection(value) === 'failed' ? 1000 : 0)
      + value.active_alerts * 10 + value.critical * 5 + (connection(value) === 'stale' ? 1 : 0);
    return priority(b) - priority(a) || a.asset_name.localeCompare(b.asset_name, 'zh-CN');
  }), [fleet, query, group, status]);
  const groups = [...new Set((result.data?.assets ?? []).map((asset) => asset.group_name || '未分组'))].sort();
  const selected = assets.find((asset) => asset.asset_id === selectedId);
  useEffect(() => {
    if (!result.loading && !selected) { clearTimeout(timer.current); setSelectedId(null); setClosing(false); }
  }, [selected, result.loading]);
  const toggle = (id: string | null) => {
    clearTimeout(timer.current);
    const next = id === selectedId ? null : id;
    if (!selectedId) { setSelectedId(next); return; }
    setClosing(true);
    timer.current = setTimeout(() => { setSelectedId(next); setClosing(false); }, 180);
  };
  const refresh = () => setRevision((value) => value + 1);
  return <section className="monitor-fleet-page">
    <div className="fleet-heading"><div><h1>服务器监控</h1><p>服务器运行状态与资源趋势</p></div>
      <div className="fleet-heading-actions"><span className="fleet-neutral-badge">SSH 无 Agent · 准实时</span>
        <Button aria-label="刷新服务器监控" icon={<RefreshCw size={16} />} loading={result.loading} onClick={refresh}>刷新</Button></div></div>
    <div className="fleet-filterbar">
      <Input aria-label="搜索服务器" prefix={<Search size={16} />} placeholder="搜索服务器名称或 IP" allowClear
        value={query} onChange={(event) => setQuery(event.target.value)} />
      <Select aria-label="服务器分组" placeholder="全部分组" allowClear value={group} onChange={setGroup}
        options={groups.map((value) => ({ value, label: value }))} />
      <Select aria-label="服务器状态" placeholder="全部状态" allowClear value={status} onChange={setStatus}
        options={Object.entries(connectionLabels).map(([value, label]) => ({ value, label }))} />
      <Select aria-label="趋势时间范围" value={hours} onChange={setHours}
        options={[1, 6, 24].map((value) => ({ value, label: `最近 ${value} 小时` }))} />
      <span className="fleet-updated">更新 {result.loading ? '读取中' : timestamp(result.data?.generated_at)}</span>
    </div>
    {result.loading ? <div className="fleet-initial-loading" role="status" aria-label="正在加载服务器监控"><Skeleton active paragraph={{ rows: 8 }} /></div>
      : result.error ? <Alert type="error" showIcon message="服务器监控读取失败" description={result.error}
        action={<Button onClick={refresh}>重试</Button>} />
        : <><FleetSummary assets={assets} />
          <div className="fleet-list-heading"><h2>服务器 <span>{assets.length}</span></h2><small>异常优先 · 点击卡片展开详情</small></div>
          {!assets.length ? <Empty description={result.data?.assets.length ? '没有匹配的服务器，可调整搜索或筛选条件' : '暂无服务器，请先在服务器资产添加服务器'} /> : null}
          <div className="fleet-server-grid">
            {assets.map((asset, index) => <Fragment key={asset.asset_id}>
              <div className="fleet-card-slot" style={{ order: index * 2 }}><ServerCard asset={asset} expanded={selectedId === asset.asset_id && !closing} onToggle={() => toggle(asset.asset_id)} /></div>
              {selected?.asset_id === asset.asset_id ? <div className={`fleet-details-slot${closing ? ' is-closing' : ''}`}
                style={{ '--desktop-order': Math.floor(index / 2) * 4 + 3, '--mobile-order': index * 2 + 1 } as CSSProperties}>
                <div className="fleet-details-clip"><ServerDetails key={asset.asset_id} asset={asset} hours={hours}
                  revision={revision} gapSeconds={result.data?.stale_after_seconds ?? 900} canManage={canManage}
                  onClose={() => toggle(null)} onRefresh={refresh} /></div>
              </div> : null}
            </Fragment>)}
          </div>
          <GlobalTrends assets={assets} hours={hours} revision={revision} />
        </>}
  </section>;
}
