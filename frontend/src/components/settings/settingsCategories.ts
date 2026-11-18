export type SettingsCategory = 'basic' | 'connection' | 'tasks' | 'notifications' | 'authentication' | 'security' | 'ai' | 'storage';
export const settingsCategories: Array<{ key: SettingsCategory; label: string; tabs: Array<[string, string]> }> = [
  { key: 'basic', label: '基本设置', tabs: [['platform', '平台信息'], ['display', '显示与默认值']] },
  { key: 'connection', label: '连接与采集', tabs: [['ssh', 'SSH 连接'], ['collection', '数据采集']] },
  { key: 'tasks', label: '任务与执行', tabs: [['queue', '任务调度'], ['terminal', '终端规则']] },
  { key: 'notifications', label: '通知设置', tabs: [['email', '邮件'], ['webhook', '消息渠道'], ['delivery', '投递规则']] },
  { key: 'authentication', label: '认证设置', tabs: [['local', '本地登录'], ['ldap', 'LDAP / AD'], ['oidc', 'OIDC']] },
  { key: 'security', label: '安全设置', tabs: [['policy', '登录与会话']] },
  { key: 'ai', label: 'AI 设置', tabs: [['model', '模型连接'], ['context', '场景与上下文']] },
  { key: 'storage', label: '数据与存储', tabs: [['cleanup', '保留与清理'], ['backup', '备份与恢复验证']] },
];
