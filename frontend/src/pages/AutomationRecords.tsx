import { Empty, Table, Tabs, Tag } from 'antd';
import type { WorkbenchData } from './automation-workbench';
import { statusLabels, statusTone } from './automation-workbench';

function status(value: string) {
  const tone = statusTone(value);
  return <Tag color={tone === 'normal' ? 'green' : tone === 'danger' ? 'red' : tone === 'warning' ? 'gold' : 'default'}>
    {statusLabels[value] ?? value}
  </Tag>;
}

export function AutomationRecords({ data, activeTab, onTabChange }: {
  data: WorkbenchData | null; activeTab: string; onTabChange: (key: string) => void;
}) {
  const missing = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="记录尚未加载，请刷新重试" />;
  return <section id="automation-records" className="automation-records" aria-label="审批与变更记录">
    <Tabs activeKey={activeTab} onChange={onTabChange} items={[
      {
        key: 'approvals', label: `审批队列${data?.approvals ? ` (${data.approvals.length})` : ''}`,
        children: data?.approvals ? <Table rowKey="id" size="small" pagination={{ pageSize: 5, hideOnSinglePage: true }}
          dataSource={data.approvals} locale={{ emptyText: '暂无审批记录' }} scroll={{ x: 480 }} columns={[
            { title: '操作', dataIndex: 'title' }, { title: '状态', dataIndex: 'status', render: status },
            { title: '申请人', dataIndex: 'requested_by' },
          ]} /> : missing,
      },
      {
        key: 'changes', label: `变更与回滚${data?.changes ? ` (${data.changes.length})` : ''}`,
        children: data?.changes ? <Table rowKey="id" size="small" pagination={{ pageSize: 5, hideOnSinglePage: true }}
          dataSource={data.changes} locale={{ emptyText: '暂无变更记录' }} scroll={{ x: 480 }} columns={[
            { title: '变更', dataIndex: 'title' }, { title: '状态', dataIndex: 'status', render: status },
            { title: '负责人', dataIndex: 'requested_by' },
          ]} /> : missing,
      },
      {
        key: 'integrations', label: `外部告警接入${data?.integrations ? ` (${data.integrations.length})` : ''}`,
        children: data?.integrations ? <Table rowKey="id" size="small" pagination={{ pageSize: 5, hideOnSinglePage: true }}
          dataSource={data.integrations} locale={{ emptyText: '暂无外部接入' }} scroll={{ x: 480 }} columns={[
            { title: '来源', dataIndex: 'name' }, { title: '类型', dataIndex: 'source_type' },
            { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{value === 'active' ? '已启用' : '已停用'}</Tag> },
          ]} /> : missing,
      },
    ]} />
  </section>;
}
