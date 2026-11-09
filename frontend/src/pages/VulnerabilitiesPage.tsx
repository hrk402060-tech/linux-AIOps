import { useEffect, useState } from 'react';
import { Alert, Button, Empty, Input, message, Pagination, Select, Spin, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ClipboardCheck, Eye, FileWarning, RefreshCw, Search } from 'lucide-react';

import { getAssets } from '../api/assets';
import { getVulnerabilities, updateVulnerabilityDisposition } from '../api/vulnerabilities';
import { VulnerabilityDetailDrawer } from '../components/VulnerabilityDetailDrawer';
import { VulnerabilityDispositionModal } from '../components/VulnerabilityDispositionModal';
import type { Asset } from '../types/asset';
import { vulnerabilitySeverityColors, vulnerabilitySeverityLabels } from '../types/vulnerability';
import type {
  VulnerabilityConclusion, VulnerabilityDispositionFilter, VulnerabilityDispositionUpdate,
  VulnerabilityFinding, VulnerabilitySeverity, VulnerabilitySummary,
} from '../types/vulnerability';

const dispositionLabels: Record<VulnerabilityDispositionFilter, string> = {
  active: '当前待处理', all: '全部历史', open: '待处理',
  pending_verification: '待复扫验证', fixed: '已修复', ignored: '已忽略',
  risk_accepted: '风险接受',
};
const emptySummary: VulnerabilitySummary = {
  critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0,
};

export function VulnerabilitiesPage({ initialAssetId = null, initialQuery = '', initialDisposition = 'active' }: {
  initialAssetId?: string | null; initialQuery?: string; initialDisposition?: VulnerabilityDispositionFilter;
}) {
  const [items, setItems] = useState<VulnerabilityFinding[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [summary, setSummary] = useState<VulnerabilitySummary>(emptySummary);
  const [query, setQuery] = useState(initialQuery);
  const [assetId, setAssetId] = useState(initialAssetId ?? 'all');
  const [severity, setSeverity] = useState<VulnerabilitySeverity | 'all'>('all');
  const [conclusion, setConclusion] = useState<VulnerabilityConclusion | 'all'>('all');
  const [disposition, setDisposition] = useState<VulnerabilityDispositionFilter>(initialDisposition);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<VulnerabilityFinding | null>(null);
  const [disposing, setDisposing] = useState<VulnerabilityFinding | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const load = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await getVulnerabilities({
        query: query.trim() || undefined, assetId: assetId === 'all' ? undefined : assetId,
        severity: severity === 'all' ? undefined : severity,
        conclusion: conclusion === 'all' ? undefined : conclusion, disposition, page, pageSize: 10,
      });
      setItems(response.items); setSummary(response.summary); setTotal(response.total);
    } catch (error) {
      const text = error instanceof Error ? error.message : '漏洞数据加载失败';
      setLoadError(text); messageApi.error(text);
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [assetId, severity, conclusion, disposition, page]);
  useEffect(() => {
    void getAssets().then(setAssets).catch(() => messageApi.error('资产列表加载失败'));
  }, []);

  const saveDisposition = async (payload: VulnerabilityDispositionUpdate) => {
    if (!disposing) return;
    setSubmitting(true);
    try {
      await updateVulnerabilityDisposition(disposing.id, payload);
      setDisposing(null); messageApi.success('处置状态已记录，漏洞证据保留'); await load();
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '漏洞处置保存失败');
    } finally { setSubmitting(false); }
  };

  const columns: ColumnsType<VulnerabilityFinding> = [
    { title: '漏洞', key: 'identity', render: (_, item) => <div className="finding-identity"><span><FileWarning size={17} /></span><div><strong>{item.cve_id}</strong><small>{item.title}</small></div></div> },
    { title: '严重度', dataIndex: 'severity', width: 90, render: (value: VulnerabilitySeverity) => <Tag color={vulnerabilitySeverityColors[value]}>{vulnerabilitySeverityLabels[value]}</Tag> },
    { title: '资产 / 软件包', key: 'target', width: 190, render: (_, item) => <div className="stacked-cell"><strong>{item.asset_name}</strong><small>{item.package_name}</small></div> },
    { title: '版本证据', key: 'versions', width: 190, render: (_, item) => <div className="stacked-cell version-cell"><strong>{item.installed_version}</strong><small>修复于 {item.fixed_version}</small></div> },
    { title: '判定', dataIndex: 'conclusion', width: 120, render: (value: VulnerabilityConclusion) => <Tag color={value === 'confirmed' ? 'green' : 'gold'}>{value === 'confirmed' ? '已确认' : '需人工确认'}</Tag> },
    { title: '处置状态', dataIndex: 'disposition_status', width: 120, render: (value: VulnerabilityFinding['disposition_status']) => <Tag color={value === 'fixed' ? 'green' : value === 'open' ? 'red' : 'gold'}>{dispositionLabels[value]}</Tag> },
    { title: '操作', key: 'actions', width: 96, align: 'right', render: (_, item) => <div className="row-actions"><Tooltip title={item.disposition_status === 'fixed' ? '已由复扫验证修复' : '处置'}><Button type="text" disabled={item.disposition_status === 'fixed'} aria-label={'处置 ' + item.cve_id} icon={<ClipboardCheck size={17} />} onClick={() => setDisposing(item)} /></Tooltip><Tooltip title="查看证据"><Button type="text" aria-label={'查看 ' + item.cve_id} icon={<Eye size={17} />} onClick={() => setSelected(item)} /></Tooltip></div> },
  ];

  return <section className="vulnerabilities-page">{contextHolder}
    <div className="page-heading"><div><p className="eyebrow">证据驱动研判</p><h1>漏洞详情</h1></div></div>
    {loadError ? <Alert type="error" showIcon message="漏洞数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}><div hidden={Boolean(loadError)} className={`vulnerabilities-data-shell${loading ? ' is-loading' : ''}`}><div className="metric-band finding-metrics">
      <div><span>当前漏洞</span><strong>{summary.critical + summary.high + summary.medium + summary.low}</strong></div>
      <div><span>严重 / 高危</span><strong>{summary.critical} / {summary.high}</strong></div>
      <div><span>已确认</span><strong>{summary.confirmed}</strong></div>
      <div><span>需人工确认</span><strong className="metric-text">{summary.manual_confirmation}</strong></div>
    </div>
    <Alert type="info" showIcon message="判定与处置状态说明" description="已确认：厂商公告与采集证据一致；需人工确认：无法可靠判断厂商 backport。待处理：尚未人工处置；待复扫验证：已人工确认变更，等待只读复扫；已修复：复扫已验证；已忽略或风险接受会保留原因、操作人和时间。" />
    <div className="finding-toolbar">
      <Input allowClear prefix={<Search size={16} />} placeholder="搜索 CVE、公告、资产或软件包" value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={() => { setPage(1); void load(); }} />
      <Select aria-label="严重度筛选" value={severity} onChange={(value) => { setPage(1); setSeverity(value); }} options={[{ value: 'all', label: '全部严重度' }, ...Object.entries(vulnerabilitySeverityLabels).map(([value, label]) => ({ value, label }))]} />
      <Select aria-label="结论筛选" value={conclusion} onChange={(value) => { setPage(1); setConclusion(value); }} options={[{ value: 'all', label: '全部判定' }, { value: 'confirmed', label: '已确认' }, { value: 'manual_confirmation', label: '需人工确认' }]} />
      <Tooltip title="刷新"><Button aria-label="刷新" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip>
    </div>
    <div className="finding-filter-row">
      <Select aria-label="资产筛选" showSearch optionFilterProp="label" value={assetId} onChange={(value) => { setPage(1); setAssetId(value); }} options={[{ value: 'all', label: '全部资产' }, ...assets.map((item) => ({ value: item.id, label: item.name + ' · ' + item.host }))]} />
      <Select aria-label="处置状态筛选" value={disposition} onChange={(value) => { setPage(1); setDisposition(value); }} options={Object.entries(dispositionLabels).map(([value, label]) => ({ value, label }))} />
    </div>
    <div className="finding-list-mobile">
      {items.map((item) => <button key={item.id} type="button" className="finding-mobile-item" onClick={() => setSelected(item)}>
        <span className="finding-mobile-top"><strong>{item.cve_id}</strong><Tag color={vulnerabilitySeverityColors[item.severity]}>{vulnerabilitySeverityLabels[item.severity]}</Tag></span>
        <span>{item.title}</span><small>{item.asset_name} · {item.package_name}</small>
        <span className="finding-mobile-bottom"><Tag color="gold">{dispositionLabels[item.disposition_status]}</Tag><small>{item.knowledge_version ?? '未发布'}</small></span>
      </button>)}
      {!loading && items.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配漏洞" /> : null}
      {total > 10 ? <Pagination current={page} pageSize={10} total={total} showSizeChanger={false} onChange={setPage} /> : null}
    </div>
    <div className="table-frame finding-table"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={{ current: page, pageSize: 10, total, showSizeChanger: false, onChange: setPage }} scroll={{ x: 1040 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配漏洞" /> }} /></div></div></Spin>
    <VulnerabilityDetailDrawer finding={selected} onClose={() => setSelected(null)} />
    <VulnerabilityDispositionModal finding={disposing} submitting={submitting} onClose={() => setDisposing(null)} onSubmit={saveDisposition} />
  </section>;
}
