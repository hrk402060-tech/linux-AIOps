import type { ReactNode } from 'react';
import { Descriptions, Tag } from 'antd';
import type { SystemStatus } from '../api/system';
import { formatPlatformDateTime } from '../utils/platformPreferences';

const duration = (seconds: number) => seconds >= 3600 ? `${Math.round(seconds / 3600)} 小时`
  : seconds >= 60 ? `${Math.round(seconds / 60)} 分钟` : `${seconds} 秒`;
export function PlatformStatus({ status }: { status: SystemStatus }) {
  const scheduler = status.diagnostic_scheduler;
  const sections: Array<{ title: string; values: Array<[string, ReactNode]> }> = [
    { title: '平台版本与存储', values: [
      ['数据库', <Tag color={status.database_status === 'ok' ? 'success' : 'error'}>{status.database_status === 'ok' ? '正常' : '不可用'}</Tag>],
      ['引擎', status.database_engine], ['结构版本', status.schema_revision ?? '未迁移'],
      ['检查时间', formatPlatformDateTime(status.checked_at)],
    ] },
    { title: '采集与终端', values: [
      ['监控采集周期', duration(status.monitoring_interval_seconds)],
      ['知识库同步周期', duration(status.knowledge_sync_interval_seconds)],
      ['SSH 最大并发', `${status.terminal.max_connections} 个`],
      ['SSH 空闲超时', duration(status.terminal.idle_timeout_seconds)],
      ['SSH 最长会话', duration(status.terminal.max_duration_seconds)],
    ] },
    { title: '安全边界', values: [
      ['扫描模式', status.security.scan_read_only_default ? '只读' : '需检查'],
      ['AI 直接执行', status.security.ai_direct_execution ? '允许' : '禁止'],
      ['凭据加密', status.security.credential_encryption], ['认证方式', status.security.authentication],
    ] },
    { title: '诊断调度', values: [
      ['调度器', ({ running: '运行中', stale: '心跳过期', error: '异常', disabled: '已停用' } as const)[scheduler.status]],
      ['最近心跳', formatPlatformDateTime(scheduler.last_loop_at)],
      ['排队 / 执行中', `${scheduler.queued} / ${scheduler.running}`],
      ['重试等待 / 活动槽位', `${scheduler.retry_wait} / ${scheduler.active_slots}`],
      ['最近错误', scheduler.last_error ?? '无'],
    ] },
  ];
  return <div className="system-grid">{sections.map((section) => <section key={section.title} className="ops-panel">
    <h2>{section.title}</h2>
    <Descriptions size="small" column={1} items={section.values.map(([label, children]) => ({ key: label, label, children }))} />
  </section>)}</div>;
}
