import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Input, message, Select, Tabs, Tooltip } from 'antd';
import { FileUp, Play, RefreshCw, Search } from 'lucide-react';

import {
  cancelKnowledgeBackfill, createKnowledgeBackfill, getKnowledgeAdvisories, getKnowledgeBackfills,
  getKnowledgeCoverage, getKnowledgeReleases, getKnowledgeSources, getKnowledgeSummary,
  getKnowledgeSyncRuns, importKnowledgePackage, resumeKnowledgeBackfill, rollbackKnowledgeRelease,
  syncKnowledgeSource,
} from '../api/knowledge';
import { KnowledgeImportDrawer } from '../components/KnowledgeImportDrawer';
import type {
  KnowledgeAdvisory, KnowledgeBackfill, KnowledgeCoverage, KnowledgeImportPayload, KnowledgeRelease,
  KnowledgeSource, KnowledgeSummary, KnowledgeSyncRun,
} from '../types/knowledge';
import { AdvisoryTable, BackfillTable, CoverageTable, ReleaseTable, SourceTable, SyncRunTable } from '../components/KnowledgeTables';

const emptySummary: KnowledgeSummary = { active_version: null, advisory_count: 0, source_count: 0, domestic_source_count: 0, last_updated_at: null };
const emptyCoverage: KnowledgeCoverage = { is_complete: false, completeness_note: '', publication_policy: 'stage_validate_publish', failure_policy: 'retain_active_release_and_alert', critical_check_interval_hours: 4, sources: [] };

export function KnowledgePage() {
  const [summary, setSummary] = useState(emptySummary);
  const [coverage, setCoverage] = useState(emptyCoverage);
  const [advisories, setAdvisories] = useState<KnowledgeAdvisory[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [releases, setReleases] = useState<KnowledgeRelease[]>([]);
  const [runs, setRuns] = useState<KnowledgeSyncRun[]>([]);
  const [backfills, setBackfills] = useState<KnowledgeBackfill[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [severity, setSeverity] = useState('all');
  const [osFamily, setOsFamily] = useState('all');
  const [messageApi, contextHolder] = message.useMessage();

  const load = async () => {
    setLoading(true);
    try {
      const result = await Promise.all([getKnowledgeSummary(), getKnowledgeAdvisories(), getKnowledgeSources(), getKnowledgeReleases(), getKnowledgeSyncRuns(), getKnowledgeCoverage(), getKnowledgeBackfills()]);
      setSummary(result[0]); setAdvisories(result[1]); setSources(result[2]); setReleases(result[3]); setRuns(result[4]); setCoverage(result[5]); setBackfills(result[6]);
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!backfills.some((item) => ['queued', 'running'].includes(item.status))) return;
    const timer = window.setInterval(() => { void getKnowledgeBackfills().then(setBackfills); }, 3000);
    return () => window.clearInterval(timer);
  }, [backfills]);

  const filtered = useMemo(() => advisories.filter((item) => {
    const keyword = query.trim().toLowerCase();
    const content = `${item.cve_id} ${item.advisory_id} ${item.title} ${item.source_name}`.toLowerCase();
    return (!keyword || content.includes(keyword)) && (severity === 'all' || item.severity === severity)
      && (osFamily === 'all' || item.os_family === osFamily);
  }), [advisories, osFamily, query, severity]);

  const importPackage = async (payload: KnowledgeImportPayload) => {
    setSubmitting(true);
    try { await importKnowledgePackage(payload); messageApi.success('签名校验通过，知识版本已激活'); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '导入失败'); throw error; }
    finally { setSubmitting(false); }
  };

  const rollback = async (release: KnowledgeRelease) => {
    try { await rollbackKnowledgeRelease(release.id); messageApi.success(`已回滚至 ${release.version}`); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '回滚失败'); }
  };

  const sync = async (source: KnowledgeSource) => {
    try { await syncKnowledgeSource(source.id); messageApi.info('同步任务已记录'); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '同步失败'); }
  };

  const startBackfill = async () => {
    try { await createKnowledgeBackfill({ batch_size: 10, delay_seconds: 2 }); messageApi.success('历史回填已启动'); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '启动失败'); }
  };

  const resumeBackfill = async (item: KnowledgeBackfill) => {
    try { await resumeKnowledgeBackfill(item.id); messageApi.success('历史回填已续跑'); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '续跑失败'); }
  };

  const cancelBackfill = async (item: KnowledgeBackfill) => {
    try { await cancelKnowledgeBackfill(item.id); messageApi.success('历史回填已取消'); await load(); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '取消失败'); }
  };

  const tabs = [
    { key: 'coverage', label: '覆盖状态', children: <><div className="knowledge-policy"><span>发布流程：暂存校验后发布</span><span>同步失败：保留上一生产版本并告警</span><span>严重情报：每 {coverage.critical_check_interval_hours} 小时增量检查</span></div><CoverageTable items={coverage.sources} loading={loading} /></> },
    { key: 'advisories', label: '公告库', children: <><div className="knowledge-toolbar"><Input allowClear prefix={<Search size={16} />} placeholder="搜索 CVE、公告号、标题或来源" value={query} onChange={(event) => setQuery(event.target.value)} /><Select value={severity} onChange={setSeverity} options={[{ value: 'all', label: '全部等级' }, { value: 'critical', label: '严重' }, { value: 'high', label: '高危' }, { value: 'medium', label: '中危' }, { value: 'low', label: '低危' }]} /><Select value={osFamily} onChange={setOsFamily} options={[{ value: 'all', label: '全部系统' }, ...Array.from(new Set(advisories.map((item) => item.os_family))).map((value) => ({ value, label: value }))]} /><Tooltip title="刷新"><Button aria-label="刷新" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></Tooltip></div><AdvisoryTable items={filtered} loading={loading} /></> },
    { key: 'sources', label: '数据源', children: <SourceTable items={sources} loading={loading} onSync={sync} /> },
    { key: 'releases', label: '发布版本', children: <ReleaseTable items={releases} loading={loading} onRollback={rollback} /> },
    { key: 'runs', label: '同步记录', children: <SyncRunTable items={runs} loading={loading} /> },
    { key: 'backfills', label: '历史回填', children: <><div className="backfill-toolbar"><div><strong>openEuler 历史公告</strong><span>逐页暂存、校验和发布，失败后从当前页续跑。</span></div><Button type="primary" icon={<Play size={16} />} disabled={backfills.some((item) => ['queued', 'running'].includes(item.status))} onClick={() => void startBackfill()}>启动历史回填</Button></div><BackfillTable items={backfills} loading={loading} onResume={resumeBackfill} onCancel={cancelBackfill} /></> },
  ];

  return <section className="knowledge-page">{contextHolder}<div className="page-heading"><div><p className="eyebrow">漏洞情报</p><h1>知识库</h1></div><Button type="primary" icon={<FileUp size={17} />} onClick={() => setDrawerOpen(true)}>导入签名包</Button></div><Alert className="knowledge-coverage-alert" type="warning" showIcon message="知识库覆盖不完整" description={coverage.completeness_note || '正在读取各数据源覆盖状态。'} /><div className="metric-band"><div><span>当前版本</span><strong className="metric-text">{summary.active_version ?? '未发布'}</strong></div><div><span>有效公告</span><strong>{summary.advisory_count}</strong></div><div><span>数据源</span><strong>{summary.source_count}</strong></div><div><span>国产来源</span><strong>{summary.domestic_source_count}</strong></div></div><Tabs defaultActiveKey="coverage" items={tabs} /><KnowledgeImportDrawer open={drawerOpen} submitting={submitting} onClose={() => setDrawerOpen(false)} onSubmit={importPackage} /></section>;
}
