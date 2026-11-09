import { useEffect, useRef, useState } from 'react';
import { Button, Descriptions, Empty, List, Modal, Space, Table, Typography, message } from 'antd';
import { Eye, X } from 'lucide-react';
import type { AuthUser } from '../../api/auth';
import {
  getAlertAnalyses, getDiagnosticJobs,
  type AlertAnalysis, type DiagnosticJob, type DiagnosticJobHistory, type ExternalAlert,
} from '../../api/externalAlerts';
import { DiagnosticJobTag, isActiveDiagnosticJob } from './DiagnosticJobTag';

export function DiagnosticJobHistoryPanel({ alert, currentUser, onCancel }: {
  alert: ExternalAlert; currentUser: Pick<AuthUser, 'role'>; onCancel: (job: DiagnosticJob) => void;
}) {
  const [history, setHistory] = useState<DiagnosticJobHistory>({ total: 0, items: [] });
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [attemptTarget, setAttemptTarget] = useState<DiagnosticJob | null>(null);
  const [attempts, setAttempts] = useState<AlertAnalysis[]>([]);
  const [attemptLoading, setAttemptLoading] = useState(false);
  const historyGeneration = useRef(0);
  const attemptGeneration = useRef(0);
  const [messages, holder] = message.useMessage();
  useEffect(() => {
    const generation = ++historyGeneration.current;
    setLoading(true);
    void getDiagnosticJobs(alert.id, (page - 1) * 10, 10).then((result) => {
      if (generation === historyGeneration.current) setHistory(result);
    }).catch((error: unknown) => {
      if (generation === historyGeneration.current) messages.error(error instanceof Error ? error.message : '诊断任务历史加载失败');
    }).finally(() => { if (generation === historyGeneration.current) setLoading(false); });
    return () => { historyGeneration.current += 1; };
  }, [alert.id, alert.latest_job?.updated_at, page]);
  useEffect(() => () => { attemptGeneration.current += 1; }, []);
  const showAttempts = async (job: DiagnosticJob) => {
    const generation = ++attemptGeneration.current;
    setAttemptTarget(job); setAttempts([]); setAttemptLoading(true);
    try {
      const result = await getAlertAnalyses(alert.id, 0, 3, job.id);
      if (generation === attemptGeneration.current) setAttempts(result);
    } catch (error) {
      if (generation === attemptGeneration.current) messages.error(error instanceof Error ? error.message : '尝试证据加载失败');
    } finally { if (generation === attemptGeneration.current) setAttemptLoading(false); }
  };
  const latestJob = alert.latest_job;
  const rows = history.items.map((job) => latestJob && latestJob.id === job.id ? latestJob : job);
  return <section>{holder}<Typography.Title level={5}>诊断任务</Typography.Title>
    <Table<DiagnosticJob> rowKey="id" size="small" loading={loading} dataSource={rows}
      locale={{ emptyText: <Empty description="暂无诊断任务" /> }} scroll={{ x: 620 }}
      pagination={{ current: page, pageSize: 10, total: history.total, showSizeChanger: false, onChange: setPage }}
      columns={[
        { title: '状态', width: 130, render: (_, job) => <DiagnosticJobTag job={job} /> },
        { title: '尝试', width: 75, render: (_, job) => `${job.attempt_count} / ${job.max_attempts}` },
        { title: '下次重试', width: 160, render: (_, job) => job.next_attempt_at ? new Date(job.next_attempt_at).toLocaleString() : '-' },
        { title: '最近错误', width: 150, render: (_, job) => job.last_error_code ?? '-' },
        { title: '操作', width: 90, render: (_, job) => <Space size={4}>
          <Button aria-label={`查看尝试 ${job.id}`} icon={<Eye size={15} />} onClick={() => void showAttempts(job)} />
          {currentUser.role !== 'auditor' && isActiveDiagnosticJob(job) ? <Button
            aria-label={`取消诊断 ${job.id}`} icon={<X size={15} />}
            disabled={loading || Boolean(job.cancellation_requested_at)} onClick={() => onCancel(job)} /> : null}
        </Space> },
      ]} />
    <Modal title="不可变尝试证据" open={attemptTarget !== null} footer={null} destroyOnHidden
      onCancel={() => { attemptGeneration.current += 1; setAttemptTarget(null); }} width={620}>
      <Descriptions size="small" column={1} items={[
        { key: 'job', label: '任务', children: attemptTarget?.id },
        { key: 'actor', label: '提交人', children: attemptTarget?.operator },
        { key: 'reason', label: '提交原因', children: attemptTarget?.reason },
        { key: 'grant', label: '授权版本', children: attemptTarget?.grant_revision ?? '未授权' },
      ]} />
      <Typography.Paragraph type="secondary">该任务最多 3 次尝试，按执行记录展示。</Typography.Paragraph>
      <List size="small" loading={attemptLoading} dataSource={attempts} locale={{ emptyText: '暂无执行尝试' }} renderItem={(item) => <List.Item>
        <List.Item.Meta title={<Space><span>尝试 {item.attempt_number ?? '-'} · {item.status}</span><span>{item.summary}</span></Space>}
          description={<><p>{item.error_code ?? '-'} · {new Date(item.started_at).toLocaleString()}</p><pre className="redacted-payload">{JSON.stringify(item.evidence, null, 2)}</pre></>} />
      </List.Item>} />
    </Modal>
  </section>;
}
