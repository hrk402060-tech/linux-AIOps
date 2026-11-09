import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, message, Select, Spin, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Download, Eye, FileOutput, RefreshCw, Repeat2, Search } from 'lucide-react';

import { createReport, downloadReport, getReports, retryReport } from '../api/reports';
import { getScanTasks } from '../api/scan-tasks';
import { ReportCreateDrawer } from '../components/ReportCreateDrawer';
import { ReportPreviewDrawer } from '../components/ReportPreviewDrawer';
import type { ReportCreate, ReportFormat, ReportStatus, ScanReport } from '../types/report';
import type { ScanTask } from '../types/scan-task';

const statusLabels: Record<ReportStatus, string> = {
  queued: '排队', generating: '生成中', completed: '完成', failed: '失败',
};
const statusColors: Record<ReportStatus, string> = {
  queued: 'blue', generating: 'processing', completed: 'green', failed: 'red',
};
const REPORT_STALE_AFTER_DAYS = 7;
const formatDate = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value)) : '暂无';

export function ReportsPage() {
  const [items, setItems] = useState<ScanReport[]>([]);
  const [scans, setScans] = useState<ScanTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [query, setQuery] = useState('');
  const [format, setFormat] = useState<ReportFormat | 'all'>('all');
  const [status, setStatus] = useState<ReportStatus | 'all'>('all');
  const [assetId, setAssetId] = useState('all');
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<ScanReport | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const load = async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [reports, tasks] = await Promise.all([getReports(), getScanTasks()]);
      setItems(reports);
      setScans(tasks.filter((item) => item.status === 'completed'));
    } catch (error) {
      const text = error instanceof Error ? error.message : '加载失败';
      setLoadError(text); messageApi.error(text);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, []);

  const assets = useMemo(() => Array.from(new Map(
    items.flatMap((item) => item.asset_snapshot).filter((item) => item.id)
      .map((item) => [item.id as string, item]),
  ).values()), [items]);
  const filtered = useMemo(() => items.filter((item) => {
    const keyword = query.trim().toLowerCase();
    const content = (item.scan_task_name + ' ' + item.asset_snapshot
      .map((asset) => (asset.name ?? '') + ' ' + (asset.host ?? '')).join(' ')).toLowerCase();
    return (!keyword || content.includes(keyword)) && (format === 'all' || item.format === format)
      && (status === 'all' || item.status === status)
      && (assetId === 'all' || item.asset_snapshot.some((asset) => asset.id === assetId));
  }), [assetId, format, items, query, status]);
  const latestCompleted = useMemo(
    () => items.filter((item) => item.status === 'completed' && item.completed_at)
      .sort((left, right) => Date.parse(right.completed_at ?? '') - Date.parse(left.completed_at ?? ''))[0] ?? null,
    [items],
  );
  const reportIsStale = Boolean(latestCompleted?.completed_at
    && Date.now() - Date.parse(latestCompleted.completed_at) > REPORT_STALE_AFTER_DAYS * 24 * 60 * 60 * 1000);

  const create = async (payload: ReportCreate) => {
    setSubmitting(true);
    try {
      await createReport(payload);
      messageApi.success('报告已生成');
      setCreateOpen(false);
      await load();
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '生成失败');
    } finally {
      setSubmitting(false);
    }
  };
  const retry = async (item: ScanReport) => {
    try {
      await retryReport(item.id);
      messageApi.success('报告已重新生成');
      await load();
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : '重试失败');
    }
  };

  const columns: ColumnsType<ScanReport> = [
    { title: '扫描报告', key: 'name', render: (_, item) => <div className="asset-identity"><span><FileOutput size={17} /></span><div><strong>{item.scan_task_name}</strong><small>{item.asset_snapshot.map((asset) => asset.name).join('、')}</small></div></div> },
    { title: '格式', dataIndex: 'format', width: 80, render: (value: ReportFormat) => value.toUpperCase() },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: ReportStatus) => <Tag color={statusColors[value]}>{statusLabels[value]}</Tag> },
    { title: '严重 / 高危', key: 'risk', width: 110, render: (_, item) => (item.vulnerability_summary.critical ?? 0) + ' / ' + (item.vulnerability_summary.high ?? 0) },
    { title: '知识库版本', dataIndex: 'knowledge_version', width: 170, render: (value: string | null) => value ?? '未发布' },
    { title: '生成时间', dataIndex: 'created_at', width: 170, render: formatDate },
    { title: '下载', dataIndex: 'download_count', width: 70 },
    { title: '操作', key: 'actions', width: 120, align: 'right', render: (_, item) => <div className="row-actions">
      <Tooltip title="预览"><Button type="text" aria-label={'预览 ' + item.scan_task_name} icon={<Eye size={16} />} onClick={() => setSelected(item)} /></Tooltip>
      {item.status === 'completed' ? <Tooltip title="下载"><Button type="text" aria-label={'下载 ' + item.scan_task_name} icon={<Download size={16} />} onClick={() => void downloadReport(item).then(load).catch(() => messageApi.error('报告下载失败'))} /></Tooltip> : null}
      {item.status === 'failed' ? <Tooltip title="重试"><Button type="text" aria-label={'重试 ' + item.scan_task_name} icon={<Repeat2 size={16} />} onClick={() => void retry(item)} /></Tooltip> : null}
    </div> },
  ];

  return <section className="reports-page">{contextHolder}
    <div className="page-heading"><div><p className="eyebrow">报告与审计</p><h1>报告中心</h1></div><Button type="primary" icon={<FileOutput size={17} />} onClick={() => setCreateOpen(true)}>生成报告</Button></div>
    {reportIsStale ? <Alert type="warning" showIcon message="报告数据已陈旧" description={`最新完成报告：${formatDate(latestCompleted?.completed_at ?? null)}，已超过 ${REPORT_STALE_AFTER_DAYS} 天；请重新执行扫描并生成报告。`} /> : null}
    {loadError ? <Alert type="error" showIcon message="报告数据读取失败" description={loadError} action={<Button onClick={() => void load()}>重试</Button>} /> : null}
    <Spin spinning={loading} tip={loading ? '正在加载真实数据' : undefined}><div hidden={Boolean(loadError)} className={`reports-data-shell${loading ? ' is-loading' : ''}`}><div className="metric-band"><div><span>报告总数</span><strong>{items.length}</strong></div><div><span>PDF</span><strong>{items.filter((item) => item.format === 'pdf').length}</strong></div><div><span>Word</span><strong>{items.filter((item) => item.format === 'docx').length}</strong></div><div><span>生成失败</span><strong className="metric-text">{items.filter((item) => item.status === 'failed').length}</strong></div></div>
    <div className="report-toolbar"><Input allowClear prefix={<Search size={16} />} placeholder="搜索任务、资产名或 IP" value={query} onChange={(event) => setQuery(event.target.value)} /><Select value={assetId} onChange={setAssetId} options={[{ value: 'all', label: '全部资产' }, ...assets.map((item) => ({ value: item.id, label: (item.name ?? '未知资产') + ' · ' + (item.host ?? '') }))]} /><Select value={format} onChange={setFormat} options={[{ value: 'all', label: '全部格式' }, { value: 'pdf', label: 'PDF' }, { value: 'docx', label: 'Word' }]} /><Select value={status} onChange={setStatus} options={[{ value: 'all', label: '全部状态' }, ...Object.entries(statusLabels).map(([value, label]) => ({ value, label }))]} /><Tooltip title="刷新"><Button aria-label="刷新" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip></div>
    <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={filtered} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1120 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无报告" /> }} /></div></div></Spin>
    <ReportCreateDrawer open={createOpen} scans={scans} submitting={submitting} onClose={() => setCreateOpen(false)} onSubmit={create} />
    <ReportPreviewDrawer report={selected} onClose={() => setSelected(null)} />
  </section>;
}
