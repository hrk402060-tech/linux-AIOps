import { useEffect, useState } from 'react';
import { Button, DatePicker, Input, Select, Space, Table, Tag, message } from 'antd';
import { RefreshCw, Search } from 'lucide-react';

import { getAuditLog, type AuditLogEntry } from '../api/audit-log';

const sources: Record<string, string> = { event_disposition: '事件处置', asset_cleanup: '资产清理', asset_lifecycle: '资产生命周期', vulnerability_disposition: '漏洞处置', report: '报告操作', ai_command_approval: 'AI 命令审批', ai_provider_config: 'AI 模型配置', alert_notification: '告警通知' };
const sourceOptions = Object.entries(sources).map(([value, label]) => ({ value, label }));

export function AuditLogPage() {
  const [items, setItems] = useState<AuditLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ ...filters, page: String(page), page_size: '20' });
      const data = await getAuditLog(params); setItems(data.items); setTotal(data.total);
    } catch (error) { messages.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [filters, page]);
  const filter = (key: string, value?: string) => {
    setPage(1); setFilters((current) => { const next = { ...current }; if (value) next[key] = value; else delete next[key]; return next; });
  };
  return <section>{holder}
    <div className="page-heading"><div><p className="eyebrow">追加式安全操作记录</p><h1>统一审计日志</h1></div><Button aria-label="刷新审计日志" icon={<RefreshCw size={17} />} onClick={() => void load()} /></div>
    <Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear aria-label="审计来源" placeholder="全部来源" style={{ width: 180 }} options={sourceOptions} onChange={(value) => filter('source', value)} />
      <Input allowClear aria-label="审计操作人" prefix={<Search size={16} />} placeholder="搜索操作人" style={{ width: 200 }} onPressEnter={(event) => filter('operator', event.currentTarget.value.trim())} onClear={() => filter('operator')} />
      <Input allowClear aria-label="审计动作" placeholder="精确动作标识" style={{ width: 180 }} onPressEnter={(event) => filter('action', event.currentTarget.value.trim())} onClear={() => filter('action')} />
      <DatePicker.RangePicker onChange={(dates) => { filter('since', dates?.[0]?.startOf('day').toISOString()); filter('until', dates?.[1]?.endOf('day').toISOString()); }} />
    </Space>
    <div className="table-frame"><Table rowKey="id" loading={loading} dataSource={items} scroll={{ x: 1050 }} pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, onChange: setPage }} columns={[
      { title: '来源', dataIndex: 'source', width: 140, render: (value: string) => <Tag>{sources[value] ?? value}</Tag> },
      { title: '动作', dataIndex: 'action', width: 150 }, { title: '对象', dataIndex: 'target' },
      { title: '详情', dataIndex: 'detail' }, { title: '操作人', dataIndex: 'operator', width: 150 },
      { title: '时间', dataIndex: 'occurred_at', width: 180, render: (value: string) => new Date(value).toLocaleString('zh-CN') },
    ]} /></div>
  </section>;
}
