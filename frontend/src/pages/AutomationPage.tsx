import { useEffect, useState } from 'react';
import { Alert, Button, Card, Col, Row, Spin, Table, Tag, message } from 'antd';
import { ArrowRight, RefreshCw, ShieldCheck } from 'lucide-react';
import type { PageKey } from '../App';
import { getScanSchedules } from '../api/scan-tasks';
import { getBaselineTasks } from '../api/baselines';
import { getExposureTasks } from '../api/exposures';
import { getCredentialRiskTasks } from '../api/credential-risks';
import { getPatchAdvice } from '../api/patches';
import { getAutomationApprovals, getChangeRecords, getExternalIntegrations, getOperationsOverview, type AutomationApproval, type ChangeRecord, type ExternalIntegration, type OperationsOverview } from '../api/operations';

type CardItem = { key: PageKey; title: string; description: string; count: number; status: string; color: string };

export function AutomationPage({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const [items, setItems] = useState<CardItem[]>([]);
  const [overview, setOverview] = useState<OperationsOverview | null>(null);
  const [approvals, setApprovals] = useState<AutomationApproval[]>([]);
  const [changes, setChanges] = useState<ChangeRecord[]>([]);
  const [integrations, setIntegrations] = useState<ExternalIntegration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true); setError('');
    try {
      const [schedules, baselines, exposures, risks, patches, summary, approvalRows, changeRows, integrationRows] = await Promise.all([getScanSchedules(), getBaselineTasks(), getExposureTasks(), getCredentialRiskTasks(), getPatchAdvice(), getOperationsOverview(), getAutomationApprovals(), getChangeRecords(), getExternalIntegrations()]);
      setOverview(summary); setApprovals(approvalRows); setChanges(changeRows); setIntegrations(integrationRows);
      setItems([
        { key: 'schedules', title: '定时扫描', description: '周期性只读漏洞扫描计划', count: schedules.length, status: schedules.filter((item) => item.enabled).length + ' 个启用', color: 'blue' },
        { key: 'baselines', title: '基线合规', description: 'Linux 基线只读检查任务', count: baselines.length, status: baselines.filter((item) => ['scanning', 'aggregating'].includes(item.status)).length + ' 个执行中', color: 'green' },
        { key: 'exposures', title: '暴露面检测', description: '服务器监听面与潜在暴露', count: exposures.length, status: exposures.filter((item) => ['scanning', 'aggregating'].includes(item.status)).length + ' 个检测中', color: 'orange' },
        { key: 'credential-risks', title: '弱口令风险', description: '离线密码策略风险评估', count: risks.length, status: risks.filter((item) => ['assessing', 'aggregating'].includes(item.status)).length + ' 个评估中', color: 'purple' },
        { key: 'patches', title: '补丁与变更', description: '厂商公告关联与人工确认', count: patches.length, status: patches.filter((item) => item.status === 'pending_approval').length + ' 个待确认', color: 'red' },
      ]);
    } catch (value) { const text = value instanceof Error ? value.message : '自动化工作台加载失败'; setError(text); messages.error(text); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  return <section className="automation-page">{holder}<div className="page-heading"><div><p className="eyebrow">任务编排与变更控制</p><h1>自动化与变更</h1></div><Button aria-label="刷新自动化工作台" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></div><Alert type="info" showIcon icon={<ShieldCheck size={18} />} message="默认只读与人工确认" description={overview?.message ?? '任务沿用现有安全边界；补丁建议必须人工确认，平台不会自动执行远程修复。'} />{error ? <Alert role="alert" type="error" message={error} action={<Button size="small" onClick={() => void load()}>重试</Button>} /> : null}<Spin spinning={loading}><Row gutter={[16, 16]} style={{ marginTop: 16 }}>{items.map((item) => <Col xs={24} sm={12} lg={8} key={item.key}><Card title={item.title} extra={<Tag color={item.color}>{item.count}</Tag>} actions={[<Button key="open" type="link" icon={<ArrowRight size={15} />} onClick={() => onNavigate(item.key)}>进入模块</Button>]}><p>{item.description}</p><Tag>{item.status}</Tag></Card></Col>)}</Row><Row gutter={[16, 16]} style={{ marginTop: 16 }}><Col xs={24} lg={8}><Card title="审批队列" extra={<Tag color={approvals.some((item) => item.status === 'pending') ? 'orange' : 'green'}>{approvals.filter((item) => item.status === 'pending').length} 待处理</Tag>}><Table rowKey="id" size="small" pagination={{ pageSize: 5 }} dataSource={approvals} locale={{ emptyText: '暂无审批记录' }} columns={[{ title: '操作', dataIndex: 'title' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{({ pending: '待审批', approved: '已批准', rejected: '已拒绝', running: '执行中', completed: '已完成', failed: '失败' } as Record<string, string>)[value] ?? value}</Tag> }, { title: '申请人', dataIndex: 'requested_by' }]} /></Card></Col><Col xs={24} lg={8}><Card title="变更与回滚" extra={<Tag>{changes.length} 条记录</Tag>}><Table rowKey="id" size="small" pagination={{ pageSize: 5 }} dataSource={changes} locale={{ emptyText: '暂无变更记录' }} columns={[{ title: '变更', dataIndex: 'title' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'rolled_back' ? 'green' : value === 'applied' ? 'blue' : 'default'}>{({ planned: '计划中', approved: '已批准', applied: '已应用', rolled_back: '已回滚', cancelled: '已取消' } as Record<string, string>)[value] ?? value}</Tag> }, { title: '负责人', dataIndex: 'requested_by' }]} /></Card></Col><Col xs={24} lg={8}><Card title="外部告警接入" extra={<Tag color={integrations.some((item) => item.status === 'active') ? 'green' : 'default'}>{integrations.filter((item) => item.status === 'active').length} 已启用</Tag>}><Table rowKey="id" size="small" pagination={{ pageSize: 5 }} dataSource={integrations} locale={{ emptyText: '暂无外部接入' }} columns={[{ title: '来源', dataIndex: 'name' }, { title: '类型', dataIndex: 'source_type' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '已启用' : '已停用'}</Tag> }]} /></Card></Col></Row></Spin></section>;
}
