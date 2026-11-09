import { Descriptions, Drawer, Table, Tag, Timeline } from 'antd';

import type { ScanReport } from '../types/report';

const severityLabels: Record<string, string> = { critical: '严重', high: '高危', medium: '中危', low: '低危' };
const dispositionLabels: Record<string, string> = { open: '待处理', pending_verification: '待验证', fixed: '已修复', ignored: '已忽略', risk_accepted: '风险接受' };

const formatDate = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value)) : '暂无';

export function ReportPreviewDrawer({ report, onClose }: {
  report: ScanReport | null; onClose: () => void;
}) {
  return <Drawer title="报告预览" width={680} open={Boolean(report)} onClose={onClose}>
    {report ? <div className="report-preview">
      <div className="finding-detail-heading"><div><strong>{report.scan_task_name}</strong><span>{report.file_name}</span></div><Tag color="green">{report.format.toUpperCase()}</Tag></div>
      <Descriptions size="small" column={2} items={[
        { key: 'assets', label: '资产', children: report.asset_snapshot.map((item) => item.name).join('、') },
        { key: 'version', label: '知识库版本', children: report.knowledge_version ?? '未发布' },
        { key: 'time', label: '生成时间', children: formatDate(report.completed_at) },
        { key: 'downloads', label: '下载次数', children: report.download_count },
      ]} />
      <h3>风险汇总</h3>
      <Table size="small" pagination={false} rowKey="level" dataSource={[
        { level: '严重', count: report.vulnerability_summary.critical ?? 0 },
        { level: '高危', count: report.vulnerability_summary.high ?? 0 },
        { level: '中危', count: report.vulnerability_summary.medium ?? 0 },
        { level: '低危', count: report.vulnerability_summary.low ?? 0 },
        { level: '需人工确认', count: report.vulnerability_summary.manual_confirmation ?? 0 },
      ]} columns={[{ title: '等级', dataIndex: 'level' }, { title: '数量', dataIndex: 'count', width: 90 }]} />
      <h3>漏洞明细</h3>
      <Table size="small" pagination={{ pageSize: 5, showSizeChanger: false }} rowKey={(item) => String(item.cve_id) + String(item.asset_id)}
        dataSource={report.finding_snapshot ?? []} scroll={{ x: 620 }} columns={[
          { title: '资产', dataIndex: 'asset_name', width: 120 },
          { title: 'CVE', dataIndex: 'cve_id', width: 140 },
          { title: '等级', dataIndex: 'severity', width: 80, render: (value: string) => severityLabels[value] ?? value },
          { title: '软件包', key: 'package', render: (_, item) => String(item.package_name ?? '') + ' ' + String(item.installed_version ?? '') },
          { title: '处置', dataIndex: 'disposition_status', width: 110, render: (value: string) => dispositionLabels[value] ?? value },
        ]} />
      <h3>文件完整性</h3>
      <div className="report-digest"><span>SHA-256</span><code>{report.sha256}</code></div>
      <h3>审计记录</h3>
      <Timeline items={report.audit_history.map((item) => ({ children: <div className="timeline-entry"><strong>{item.detail}</strong><span>{item.operator}</span><small>{formatDate(item.occurred_at)}</small></div> }))} />
    </div> : null}
  </Drawer>;
}
