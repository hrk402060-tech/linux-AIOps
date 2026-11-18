import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Descriptions, Input, Table, Tag } from 'antd';
import { Archive, ListFilter, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { createSettingsDataJob, getCleanupPreview, getSettingsDataJobs, type CleanupPreview, type SettingsDataJob } from '../../api/settingsData';
import { formatPlatformDateTime } from '../../utils/platformPreferences';

const statuses = { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败' };
const kinds = { cleanup: '历史清理', backup: '创建备份', verify_backup: '恢复验证' };
export function StorageSettingsPanel({ tab }: { tab: 'cleanup' | 'backup' }) {
  const [preview, setPreview] = useState<CleanupPreview | null>(null);
  const [items, setItems] = useState<SettingsDataJob[]>([]);
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const generation = useRef(0);
  const scopeGeneration = useRef(0);
  const mounted = useRef(false);
  const mutation = useRef(false);
  const load = async (report = true) => {
    const sequence = ++generation.current;
    if (report) { setLoading(true); setError(''); }
    try {
      const result = await getSettingsDataJobs();
      if (sequence === generation.current) setItems(result.items);
    } catch (cause) {
      if (sequence === generation.current) setError(cause instanceof Error ? cause.message : '数据任务加载失败');
    } finally { if (sequence === generation.current) setLoading(false); }
  };
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; generation.current += 1; scopeGeneration.current += 1; };
  }, []);
  useEffect(() => {
    scopeGeneration.current += 1;
    setConfirmation(''); setNotice(''); setPreview(null); setError('');
  }, [tab]);
  useEffect(() => {
    if (!items.some((item) => ['queued', 'running'].includes(item.status))) return;
    const timer = window.setInterval(() => void load(false), 4000);
    return () => window.clearInterval(timer);
  }, [items]);
  const inspect = async () => {
    if (mutation.current) return;
    const scope = scopeGeneration.current;
    mutation.current = true; setWorking(true); setError('');
    try {
      const result = await getCleanupPreview();
      if (mounted.current && scope === scopeGeneration.current) setPreview(result);
    } catch (cause) {
      if (mounted.current && scope === scopeGeneration.current) setError(cause instanceof Error ? cause.message : '清理范围统计失败');
    } finally { mutation.current = false; if (mounted.current) setWorking(false); }
  };
  const enqueue = async (kind: SettingsDataJob['kind'], backupId?: string) => {
    if (mutation.current) return;
    const scope = scopeGeneration.current;
    mutation.current = true; setWorking(true); setError(''); setNotice('');
    try {
      const job = await createSettingsDataJob({ kind, confirmation, ...(backupId ? { backup_job_id: backupId } : {}) });
      if (!mounted.current) return;
      setItems((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      if (scope !== scopeGeneration.current) return;
      setConfirmation(''); setNotice(`${kinds[kind]}已提交后台执行`);
      if (kind === 'cleanup') setPreview(null);
    } catch (cause) {
      if (mounted.current && scope === scopeGeneration.current) setError(cause instanceof Error ? cause.message : '数据任务提交失败');
    } finally { mutation.current = false; if (mounted.current) setWorking(false); }
  };
  const visible = items.filter((item) => tab === 'cleanup' ? item.kind === 'cleanup' : item.kind !== 'cleanup');
  return <section className="settings-section" aria-label="数据后台任务">
    <h3>{tab === 'cleanup' ? '清理预览与执行' : '备份任务与恢复验证'}</h3>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} /> : null}
    {tab === 'cleanup' ? <>
      <Button icon={<ListFilter size={16} />} disabled={working} onClick={() => void inspect()}>统计清理范围</Button>
      {preview ? <Descriptions className="settings-result" column={1} size="small" items={[
        { key: 'cutoff', label: '截止时间', children: formatPlatformDateTime(preview.cutoff_at) },
        ...preview.counts.map((entry) => ({ key: entry.resource, label: entry.label, children: `${entry.count} 条` })),
        { key: 'total', label: '合计', children: `${preview.total} 条` },
      ]} /> : null}
    </> : null}
    {tab === 'backup' ? <Alert showIcon type="info" message="恢复验证使用隔离 SQLite，验证解密、结构与数据；生产同引擎恢复演练仍待实施。" /> : null}
    <div className="settings-job-actions">
      <Input aria-label={tab === 'cleanup' ? '输入 delete 确认清理' : '输入 yes 确认备份操作'}
        placeholder={tab === 'cleanup' ? 'delete' : 'yes'} value={confirmation} disabled={working}
        autoComplete="off" onChange={(event) => setConfirmation(event.target.value)} />
      {tab === 'cleanup' ? <Button danger icon={<Trash2 size={16} />} loading={working}
        disabled={!preview || !preview.total || confirmation !== 'delete'}
        onClick={() => void enqueue('cleanup')}>执行清理</Button>
        : <Button icon={<Archive size={16} />} loading={working} disabled={confirmation !== 'yes'}
          onClick={() => void enqueue('backup')}>创建备份</Button>}
      <Button aria-label="刷新数据任务" icon={<RefreshCw size={16} />} loading={loading} disabled={working} onClick={() => void load()} />
    </div>
    <Table<SettingsDataJob> rowKey="id" loading={loading} dataSource={visible} size="small" scroll={{ x: 700 }}
      pagination={{ pageSize: 8, showSizeChanger: false }} columns={[
        { title: '任务', dataIndex: 'kind', render: (value: SettingsDataJob['kind']) => kinds[value] },
        { title: '状态', dataIndex: 'status', render: (value: SettingsDataJob['status']) =>
          <Tag color={value === 'completed' ? 'success' : value === 'failed' ? 'error' : 'default'}>{statuses[value]}</Tag> },
        { title: '创建时间', dataIndex: 'created_at', render: (value: string) => formatPlatformDateTime(value) },
        { title: '结果', render: (_, item) => item.error_code ?? (item.kind === 'cleanup'
          ? item.result.total === undefined ? '—' : `${item.result.total} 条`
          : item.result.verified ? '隔离恢复验证通过' : '待验证') },
        { title: '操作', render: (_, item) => item.kind === 'backup' && item.status === 'completed'
          ? <Button icon={<ShieldCheck size={15} />} disabled={working || confirmation !== 'yes'}
            onClick={() => void enqueue('verify_backup', item.id)}>重新验证</Button> : null },
      ]} expandable={{ expandedRowRender: (item) => <div className="settings-job-result">
        <span>任务 ID：<code>{item.id}</code></span>
        {item.result.filename ? <span>文件：<code>{item.result.filename}</code></span> : null}
        {item.result.sha256 ? <span>SHA-256：<code>{item.result.sha256}</code></span> : null}
        {item.result.table_count !== undefined ? <span>表：{item.result.table_count}，记录：{item.result.row_count}，大小：{item.result.bytes} 字节</span> : null}
        {item.result.requires_original_master_key ? <span>恢复需要原凭据主密钥，密钥不包含在备份中。</span> : null}
      </div> }} />
  </section>;
}
