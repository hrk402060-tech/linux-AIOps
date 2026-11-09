import { Descriptions, Drawer, Empty, Progress, Table, Tag, Timeline } from 'antd';

import type { ScanStatus, ScanTask } from '../types/scan-task';

const labels: Record<ScanStatus, string> = {
  queued: '排队', scanning: '扫描中', aggregating: '汇总', completed: '完成',
  failed: '失败', cancelled: '取消',
};
const formatDate = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value));

export function ScanTaskDetailDrawer({ task, onClose }: { task: ScanTask | null; onClose: () => void }) {
  const summary = task?.result_summary;
  const services = summary?.asset_results?.flatMap((asset) =>
    (asset.services ?? []).map((service) => ({ ...service, asset_name: asset.asset_name }))) ?? [];
  return (
    <Drawer title="任务详情" open={Boolean(task)} onClose={onClose} width={520}>
      {task ? <div className="scan-detail">
        <div className="scan-detail-heading">
          <div><strong>{task.name}</strong><span>{task.asset_snapshot.map((item) => item.name).join('、')}</span></div>
          <Progress type="circle" percent={task.progress} size={58} />
        </div>
        <Descriptions column={1} size="small" bordered items={[
          { key: 'mode', label: '执行模式', children: <Tag color="green">只读 / 非破坏</Tag> },
          { key: 'knowledge', label: '知识版本', children: task.knowledge_version ?? '未发布' },
          { key: 'rerun', label: '任务来源', children: task.rerun_of_id ? '历史任务重跑' : '手动创建' },
        ]} />
        <h3>风险汇总</h3>
        {summary ? <div className="risk-summary">
          <span><b>{summary.critical}</b>严重</span><span><b>{summary.high}</b>高危</span>
          <span><b>{summary.medium}</b>中危</span><span><b>{summary.low}</b>低危</span>
          <span><b>{summary.manual_confirmation}</b>需确认</span>
        </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="任务完成后生成汇总" />}
        {summary ? <p className="coverage-note">采集覆盖 {summary.assets_scanned}/{summary.assets_total}，失败 {summary.assets_failed}</p> : null}
        <h3>服务证据</h3>
        {services.length ? <Table rowKey={(item) => `${item.asset_name}-${item.listen}-${item.pid}`} size="small" pagination={false} dataSource={services} columns={[
          { title: '监听地址', dataIndex: 'listen' },
          { title: '进程', dataIndex: 'process_name', render: (value: string, item) => value || item.process || '未知' },
          { title: '所属包', dataIndex: 'package_name', render: (value: string) => value || '未映射' },
          { title: '版本', dataIndex: 'installed_version', render: (value: string) => value || '未知' },
          { title: '服务指纹', dataIndex: 'banner', render: (value: string | undefined) => value || '未读取' },
        ]} scroll={{ x: 680 }} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未采集到可映射的监听服务" />}
        <h3>执行轨迹</h3>
        <Timeline items={task.execution_log.map((item) => ({
          color: item.status === 'failed' ? 'red' : item.status === 'cancelled' ? 'gray' : 'green',
          children: <div className="timeline-entry"><strong>{labels[item.status]}</strong><span>{item.message}</span><small>{formatDate(item.occurred_at)}</small></div>,
        }))} />
      </div> : null}
    </Drawer>
  );
}
