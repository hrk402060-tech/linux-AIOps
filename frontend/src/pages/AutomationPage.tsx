import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Dropdown, Empty, Input, Pagination, Progress, Segmented, Skeleton, Spin, Switch, Tooltip } from 'antd';
import { ArrowRight, CalendarCheck, ChevronRight, Clock3, ListChecks, LoaderCircle, PanelRightClose, Plus, RadioTower, RefreshCw, Search } from 'lucide-react';
import type { PageKey } from '../App';
import { AutomationRecords } from './AutomationRecords';
import {
  buildWorkbenchTasks, filterWorkbenchTasks, formatWorkbenchTime, loadWorkbenchData,
  moduleEntries, statusLabels, statusTone, type WorkbenchData, type WorkbenchTask,
} from './automation-workbench';
import './automation-workbench.css';

function TaskDetail({ task, open, onClose, onNavigate }: {
  task: WorkbenchTask | undefined; open: boolean; onClose: () => void; onNavigate: (page: PageKey) => void;
}) {
  return <div className="automation-detail-track">
    <section className="automation-detail" role="region" aria-label="任务详情" aria-hidden={!open}>
      {task ? <>
        <header>
          <span className="automation-detail-symbol"><ListChecks size={21} /></span>
          <div><span className="automation-module-label">{task.module}</span><h2>{task.name}</h2></div>
          <Tooltip title="收起详情"><Button type="text" aria-label="收起详情" tabIndex={open ? 0 : -1}
            icon={<PanelRightClose size={18} />} onClick={onClose} /></Tooltip>
        </header>
        <span className={`automation-state ${statusTone(task.status)}`}>{statusLabels[task.status] ?? task.status}</span>
        <dl className="automation-facts">
          <div><dt>任务编号</dt><dd>{task.id}</dd></div>
          <div><dt>任务类型</dt><dd>{task.recordTab === 'approvals' ? '审批申请' : task.recordTab === 'changes' ? '变更记录'
            : task.category === 'change' ? '补丁建议' : task.module === '定时扫描' ? '定时计划' : '检查任务'}</dd></div>
          {task.facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '—'}</dd></div>)}
          <div><dt>更新时间</dt><dd>{formatWorkbenchTime(task.updatedAt)}</dd></div>
        </dl>
        {task.progress !== null ? <div className="automation-progress"><span>执行进度</span><Progress percent={task.progress}
          status={task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : 'normal'} strokeColor="#258477" /></div> : null}
        {task.error ? <Alert type="error" showIcon message={task.error} /> : null}
        {task.logs.length ? <section className="automation-execution-log" aria-label="执行记录"><h3>执行记录</h3>
          <ol>{task.logs.map((log, index) => <li key={`${log.occurred_at}:${index}`}>
            <time>{formatWorkbenchTime(log.occurred_at)}</time><span>{log.message}</span>
          </li>)}</ol>
        </section> : null}
        <footer><Button type="primary" disabled={!open} icon={<ArrowRight size={15} />} onClick={() => onNavigate(task.page)}>
          {task.recordTab === 'approvals' ? '查看审批记录' : task.recordTab === 'changes' ? '查看变更记录'
            : task.category === 'change' ? '查看补丁建议' : '查看执行记录'}
        </Button></footer>
      </> : null}
    </section>
  </div>;
}

export function AutomationPage({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const [data, setData] = useState<WorkbenchData | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [pendingOnly, setPendingOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [recordTab, setRecordTab] = useState('approvals');
  const dataRef = useRef(data);
  const requestVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    const result = await loadWorkbenchData(dataRef.current);
    if (version !== requestVersion.current) return;
    dataRef.current = result.data;
    setData(result.data);
    setErrors(result.errors);
    setLoading(false);
  }, []);
  useEffect(() => { void load(); return () => { requestVersion.current += 1; }; }, [load]);
  const tasks = useMemo(() => buildWorkbenchTasks(data), [data]);
  const filtered = useMemo(() => filterWorkbenchTasks(tasks, search, category, pendingOnly), [tasks, search, category, pendingOnly]);
  const selected = tasks.find((task) => task.key === selectedKey);
  useEffect(() => { setPage(1); setDetailOpen(false); }, [search, category, pendingOnly]);
  const pageSize = 8;
  const currentPage = Math.min(page, Math.max(1, Math.ceil(filtered.length / pageSize)));
  const pageRows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const selectionVisible = pageRows.some((task) => task.key === selectedKey);
  const showDetail = detailOpen && !!selected && selectionVisible;
  useEffect(() => {
    if (!selectionVisible) {
      setDetailOpen(false);
      if (document.activeElement?.closest('.automation-detail')) document.getElementById('automation-queue-title')?.focus();
    }
  }, [selectionVisible]);
  const closeDetail = () => {
    setDetailOpen(false);
    document.getElementById(`automation-row-${selectedKey}`)?.focus();
  };
  const metrics = [
    { key: 'schedules', label: '启用计划', count: data?.schedules?.filter((item) => item.enabled).length, icon: CalendarCheck },
    { key: 'running', label: '执行中检查', count: data && data.baselines && data.exposures && data.risks
      ? tasks.filter((item) => ['scanning', 'assessing', 'aggregating'].includes(item.status)).length : null, icon: LoaderCircle },
    { key: 'pending', label: '待确认补丁', count: data?.patches?.filter((item) => ['pending_approval', 'manual_confirmation'].includes(item.status)).length, icon: Clock3 },
    { key: 'integrations', label: '启用告警来源', count: data?.integrations?.filter((item) => item.status === 'active').length, icon: RadioTower },
  ];

  return <section className="automation-page automation-workbench" aria-busy={loading}>
    <div className="page-heading">
      <h1>自动化任务</h1>
      <div className="automation-heading-actions">
        <Button aria-label="刷新自动化任务" icon={<RefreshCw size={16} />} loading={loading} onClick={() => void load()}>刷新</Button>
        <Dropdown trigger={['click']} menu={{ items: moduleEntries.map((entry) => ({
          key: entry.key, label: entry.label, onClick: () => onNavigate(entry.key),
        })) }}>
          <Button type="primary" icon={<Plus size={16} />}>新建任务</Button>
        </Dropdown>
      </div>
    </div>
    {errors.length ? <Alert role="alert" type="warning" showIcon
      message={`部分数据加载失败：${errors.join('、')}`}
      description={data && tasks.length ? '可用来源继续展示；失败来源保留上次数据或显示未知，请刷新重试。' : '请刷新重试，失败来源不计为零或正常。'}
      action={<Button size="small" onClick={() => void load()} loading={loading}>重试</Button>} /> : null}
    <div className="automation-metrics">
      {metrics.map(({ key, label, count, icon: Icon }) => <div key={key} data-testid={`automation-metric-${key}`}>
        <div><span>{label}</span><strong>{count ?? '—'}</strong></div><Icon size={22} aria-hidden="true" />
      </div>)}
    </div>
    <nav className="automation-module-shortcuts" aria-label="任务模块入口">
      {moduleEntries.map((entry) => <Button type="text" key={entry.key} aria-label={`进入${entry.label}`} onClick={() => onNavigate(entry.key)}>
        {entry.label}<span>{data?.[entry.dataKey]?.length ?? '—'}</span><ChevronRight size={13} />
      </Button>)}
    </nav>
    <div className="automation-controls">
      <Input aria-label="搜索任务" allowClear prefix={<Search size={16} />} placeholder="搜索任务名称、目标或执行记录"
        value={search} onChange={(event) => setSearch(event.target.value)} />
      <Segmented aria-label="任务类型" value={category} onChange={(value) => setCategory(String(value))}
        options={[{ label: '全部', value: 'all' }, { label: '检查', value: 'check' }, { label: '变更', value: 'change' }]} />
      <label className="automation-pending"><Switch size="small" aria-label="仅看待确认" checked={pendingOnly} onChange={setPendingOnly} />仅看待确认</label>
    </div>
    <Spin spinning={loading}>
      <div className={`automation-console${showDetail ? ' is-detail-open' : ''}`} data-testid="automation-console">
        <section className="automation-queue" aria-label="任务队列">
          <header><h2 id="automation-queue-title" tabIndex={-1}>任务队列</h2><span>{data ? `${filtered.length} 项已加载记录` : '加载中…'}</span></header>
          {!data && loading ? <div className="automation-queue-skeleton"><Skeleton active paragraph={{ rows: 5 }} /></div>
            : filtered.length ? <div className="automation-queue-list">
              {pageRows.map((task, index) => <button type="button"
                className="automation-queue-row" id={`automation-row-${task.key}`} key={task.key}
                aria-label={`查看任务：${task.name}`} aria-pressed={showDetail && selectedKey === task.key}
                onClick={() => { setSelectedKey(task.key); setDetailOpen(true); }}>
                <span className="automation-row-index">{String((currentPage - 1) * pageSize + index + 1).padStart(2, '0')}</span>
                <span className="automation-row-name"><strong>{task.name}</strong><small>{task.module} · {task.target}</small></span>
                <span className="automation-row-meta"><span className={`automation-state ${statusTone(task.status)}`}>{statusLabels[task.status] ?? task.status}</span>
                  <time>{formatWorkbenchTime(task.updatedAt)}</time></span>
                <ChevronRight size={15} aria-hidden="true" />
              </button>)}
            </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={search || category !== 'all' || pendingOnly ? '没有符合条件的任务' : '暂无任务，可新建任务或进入模块创建'} />}
          <Pagination size="small" hideOnSinglePage showSizeChanger={false} pageSize={pageSize} current={currentPage}
            total={filtered.length} onChange={(next) => { setPage(next); setDetailOpen(false); }} />
        </section>
        <TaskDetail task={selected} open={showDetail} onClose={closeDetail} onNavigate={(target) => {
          if (selected?.recordTab) {
            setRecordTab(selected.recordTab);
            document.getElementById('automation-records')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
          } else onNavigate(target);
        }} />
      </div>
      <AutomationRecords data={data} activeTab={recordTab} onTabChange={setRecordTab} />
    </Spin>
  </section>;
}
