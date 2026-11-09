import { Button, Empty, Popconfirm, Progress, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ExternalLink, Pause, Play, RefreshCw, RotateCcw } from 'lucide-react';

import type { KnowledgeAdvisory, KnowledgeBackfill, KnowledgeCoverageSource, KnowledgeRelease, KnowledgeSource, KnowledgeSyncRun } from '../types/knowledge';

const severityLabels: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危', unknown: '未知' };
const severityColors: Record<string, string> = { critical: 'magenta', high: 'red', medium: 'orange', low: 'blue', unknown: 'default' };
const formatDate = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '暂无';
const formatSchedule = (value: string | null) => {
  if (!value) return '手动导入';
  const parts = value.split(' ');
  return parts.length === 5 ? `每日 ${parts[1].padStart(2, '0')}:${parts[0].padStart(2, '0')}` : value;
};
const empty = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />;
const healthLabels: Record<string, string> = { healthy: '正常', stale: '数据陈旧', failed: '同步失败', never_synced: '从未同步' };
const healthColors: Record<string, string> = { healthy: 'green', stale: 'orange', failed: 'red', never_synced: 'default' };
const backfillLabels: Record<string, string> = { queued: '排队', running: '回填中', completed: '完成', failed: '失败', cancelled: '已取消' };
const backfillColors: Record<string, string> = { queued: 'blue', running: 'processing', completed: 'green', failed: 'red', cancelled: 'default' };

export function BackfillTable({ items, loading, onResume, onCancel }: {
  items: KnowledgeBackfill[]; loading: boolean;
  onResume: (item: KnowledgeBackfill) => Promise<void>;
  onCancel: (item: KnowledgeBackfill) => Promise<void>;
}) {
  const columns: ColumnsType<KnowledgeBackfill> = [
    { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={backfillColors[value]}>{backfillLabels[value]}</Tag> },
    { title: '进度', key: 'progress', width: 260, render: (_, item) => <div className="backfill-progress"><Progress percent={item.progress} size="small" status={item.status === 'failed' ? 'exception' : undefined} /><small>第 {item.current_page} / {item.total_pages ?? '?'} 页</small></div> },
    { title: '已处理公告', key: 'notices', width: 150, render: (_, item) => `${item.processed_notices} / ${item.total_pages ? item.total_pages * item.batch_size : '?'} 条公告` },
    { title: '已发布记录', dataIndex: 'published_advisories', width: 130, render: (value: number) => `${value} 条 CVE` },
    { title: '结果', key: 'result', render: (_, item) => item.error_message ?? (item.status === 'completed' ? '历史公告回填完成' : '按页校验并发布') },
    { title: '操作', key: 'actions', width: 82, align: 'right', render: (_, item) => <div className="row-actions">{['failed', 'cancelled'].includes(item.status) ? <Tooltip title="续跑历史回填"><Button type="text" aria-label="续跑历史回填" icon={<Play size={16} />} onClick={() => void onResume(item)} /></Tooltip> : null}{['queued', 'running'].includes(item.status) ? <Tooltip title="取消历史回填"><Button type="text" danger aria-label="取消历史回填" icon={<Pause size={16} />} onClick={() => void onCancel(item)} /></Tooltip> : null}</div> },
  ];
  return <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={false} scroll={{ x: 980 }} locale={{ emptyText: empty }} /></div>;
}

export function CoverageTable({ items, loading }: { items: KnowledgeCoverageSource[]; loading: boolean }) {
  const columns: ColumnsType<KnowledgeCoverageSource> = [
    { title: '发行版 / 数据源', key: 'source', width: 250, render: (_, item) => <div className="stacked-cell"><strong>{item.source_name}</strong><small>{item.provider} · {item.os_families.join('、') || '暂无已发布系统'}</small></div> },
    { title: '公告数', dataIndex: 'advisory_count', width: 90 },
    { title: '最新公告', dataIndex: 'latest_advisory_at', width: 180, render: formatDate },
    { title: '最后成功同步', dataIndex: 'last_success_at', width: 180, render: formatDate },
    { title: '同步方式', dataIndex: 'sync_mode', width: 130, render: (value: string) => value === 'offline_package' ? '离线签名包' : value === 'vendor_official_api' ? '厂商官方接口' : '在线厂商源' },
    { title: '同步策略', key: 'policy', width: 150, render: (_, item) => <div className="stacked-cell"><strong>{item.daily_schedule ? formatSchedule(item.daily_schedule) : '按签名包发布'}</strong><small>{item.critical_check_interval_hours ? `严重情报每 ${item.critical_check_interval_hours} 小时` : '无稳定 API'}</small></div> },
    { title: '健康状态', dataIndex: 'health', width: 110, render: (value: string) => <Tag color={healthColors[value]}>{healthLabels[value]}</Tag> },
  ];
  return <div className="table-frame"><Table rowKey="source_id" columns={columns} dataSource={items} loading={loading} pagination={false} scroll={{ x: 1090 }} locale={{ emptyText: empty }} /></div>;
}

export function AdvisoryTable({ items, loading }: { items: KnowledgeAdvisory[]; loading: boolean }) {
  const columns: ColumnsType<KnowledgeAdvisory> = [
    { title: '漏洞 / 公告', key: 'identity', width: 230, render: (_, item) => <div className="stacked-cell"><strong>{item.cve_id}</strong><small>{item.advisory_id}</small></div> },
    { title: '标题与来源', key: 'title', render: (_, item) => <div className="stacked-cell"><strong>{item.title}</strong><small>{item.source_name}</small></div> },
    { title: '系统', dataIndex: 'os_family', width: 120 },
    { title: '等级', dataIndex: 'severity', width: 90, render: (value: string) => <Tag color={severityColors[value]}>{severityLabels[value]}</Tag> },
    { title: '软件包证据', key: 'packages', width: 300, render: (_, item) => <div className="package-evidence">{item.affected_packages.map((part) => <span key={`${part.name}-${part.fixed_version}`}><b>{part.name}</b><small>{part.affected_version} → {part.fixed_version}</small>{part.os_versions.length ? <small>适用：{part.os_versions.join('、')}</small> : null}</span>)}</div> },
    { title: '厂商公告', key: 'link', width: 90, align: 'center', render: (_, item) => <Tooltip title="打开厂商公告"><Button type="link" href={item.vendor_url} target="_blank" rel="noreferrer" aria-label={`打开 ${item.advisory_id}`} icon={<ExternalLink size={16} />} /></Tooltip> },
  ];
  return <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1060 }} locale={{ emptyText: empty }} /></div>;
}

export function SourceTable({ items, loading, onSync }: { items: KnowledgeSource[]; loading: boolean; onSync: (item: KnowledgeSource) => Promise<void> }) {
  const columns: ColumnsType<KnowledgeSource> = [
    { title: '数据源', key: 'name', render: (_, item) => <div className="stacked-cell"><strong>{item.name}</strong><small>{item.provider}</small></div> },
    { title: '接入方式', key: 'sync_mode', width: 150, render: (_, item) => <Tag color={item.sync_mode === 'offline_package' ? 'gold' : 'blue'}>{item.sync_mode === 'offline_package' ? '离线签名包' : item.sync_mode === 'vendor_official_api' ? '官方 HTTPS 接口' : '在线次要源'}</Tag> },
    { title: '优先级', dataIndex: 'priority', width: 100, render: (value: number) => value <= 10 ? '厂商优先' : '补充来源' },
    { title: '计划', key: 'schedule', width: 150, render: (_, item) => item.online_url ? formatSchedule(item.schedule) : item.sync_mode === 'offline_package' ? '手动导入' : '待配置' },
    { title: '最近同步', dataIndex: 'last_sync_at', width: 180, render: formatDate },
    { title: '操作', key: 'action', width: 76, align: 'right', render: (_, item) => item.sync_mode !== 'offline_package' ? <Tooltip title={item.online_url ? '立即同步' : '连接器待配置'}><Button type="text" disabled={!item.online_url} aria-label={`同步 ${item.name}`} icon={<RefreshCw size={16} />} onClick={() => void onSync(item)} /></Tooltip> : null },
  ];
  return <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={false} scroll={{ x: 880 }} locale={{ emptyText: empty }} /></div>;
}

export function ReleaseTable({ items, loading, onRollback }: { items: KnowledgeRelease[]; loading: boolean; onRollback: (item: KnowledgeRelease) => Promise<void> }) {
  const columns: ColumnsType<KnowledgeRelease> = [
    { title: '版本', key: 'version', render: (_, item) => <div className="stacked-cell"><strong>{item.version}</strong><small>{item.name}</small></div> },
    { title: '来源', dataIndex: 'source_name' },
    { title: '公告数', dataIndex: 'advisory_count', width: 90 },
    { title: '来源校验证据', key: 'signature', width: 230, render: (_, item) => <div className="stacked-cell"><strong>{item.signature_key_id === 'official-https-api' ? '官方 HTTPS 接口' : item.signature_key_id}</strong><small className="digest">SHA-256 {item.content_sha256}</small></div> },
    { title: '状态', dataIndex: 'status', width: 90, render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '当前' : '历史'}</Tag> },
    { title: '发布时间', dataIndex: 'created_at', width: 180, render: formatDate },
    { title: '操作', key: 'action', width: 76, align: 'right', render: (_, item) => item.status === 'archived' ? <Popconfirm title={`回滚至 ${item.version}`} description="后续扫描将使用该版本的公告快照。" okText="回滚" cancelText="取消" onConfirm={() => void onRollback(item)}><Tooltip title="回滚"><Button type="text" aria-label={`回滚 ${item.version}`} icon={<RotateCcw size={16} />} /></Tooltip></Popconfirm> : null },
  ];
  return <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={false} scroll={{ x: 1040 }} locale={{ emptyText: empty }} /></div>;
}

export function SyncRunTable({ items, loading }: { items: KnowledgeSyncRun[]; loading: boolean }) {
  const columns: ColumnsType<KnowledgeSyncRun> = [
    { title: '数据源', key: 'source', render: (_, item) => <div className="stacked-cell"><strong>{item.source_name}</strong><small>{item.filename ?? item.mode}</small></div> },
    { title: '方式', dataIndex: 'mode', width: 130, render: (value: string) => value === 'offline_package' ? '离线导入' : value === 'official_https_api' ? '官方接口' : '在线同步' },
    { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'completed' ? 'green' : value === 'failed' ? 'red' : 'blue'}>{value === 'completed' ? '完成' : value === 'failed' ? '失败' : '进行中'}</Tag> },
    { title: '公告数', dataIndex: 'advisory_count', width: 90 },
    { title: '结果', dataIndex: 'message' },
    { title: '开始时间', dataIndex: 'started_at', width: 180, render: formatDate },
  ];
  return <div className="table-frame"><Table rowKey="id" columns={columns} dataSource={items} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 920 }} locale={{ emptyText: empty }} /></div>;
}
