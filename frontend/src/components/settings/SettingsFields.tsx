import { Form, Input, InputNumber, Select, Switch } from 'antd';
import type { SettingsCategory } from './settingsCategories';

const numberField = (name: string, label: string, min: number, max: number) => <Form.Item
  name={name} label={label} rules={[{ required: true, type: 'integer', min, max, message: `请输入 ${min} 至 ${max} 的整数` }]}>
  <InputNumber min={min} max={max} precision={0} />
</Form.Item>;

export function SettingsFields({ category, tab }: { category: SettingsCategory; tab: string }) {
  return <div className="settings-form-grid">
    {category === 'basic' && tab === 'platform' ? <>
      <Form.Item name="platform_name" label="平台名称" rules={[{ required: true, whitespace: true, max: 120 }]}><Input maxLength={120} /></Form.Item>
      <Form.Item name="timezone" label="时区" rules={[{ required: true }]}><Select showSearch options={
        ['Asia/Shanghai', 'UTC', 'Asia/Tokyo', 'Asia/Singapore', 'Europe/London', 'Europe/Paris', 'America/New_York', 'America/Los_Angeles']
          .map((value) => ({ value, label: value }))
      } /></Form.Item>
    </> : null}
    {category === 'basic' && tab === 'display' ? <>
      <Form.Item name="display_density" label="显示密度" rules={[{ required: true }]}><Select options={[{ value: 'comfortable', label: '舒适' }, { value: 'compact', label: '紧凑' }]} /></Form.Item>
      <Form.Item name="default_page" label="默认页面" rules={[{ required: true }]}><Select options={[{ value: 'dashboard', label: '综合总览' }, { value: 'server-monitoring', label: '服务器监控' }]} /></Form.Item>
    </> : null}
    {category === 'connection' && tab === 'ssh' ? <>
      {numberField('connect_timeout_seconds', '连接超时（秒）', 5, 120)}
      {numberField('heartbeat_seconds', '心跳周期（秒，0 为关闭）', 0, 3600)}
      {numberField('retry_count', '失败重试次数', 0, 5)}
      <Form.Item name="host_key_policy" label="主机密钥策略"><Select options={[
        { value: 'accept_first_memory', label: '首次连接自动接受（仅内存）' },
        { value: 'registered_only', label: '严格校验已登记密钥' },
      ]} /></Form.Item>
    </> : null}
    {category === 'connection' && tab === 'collection' ? <>
      {numberField('collection_interval_seconds', '采集周期（秒）', 60, 86400)}
      <Form.Item name="collection_scope" label="采集范围"><Select options={[{ value: 'enabled_assets', label: '所有已启用资产' }, { value: 'production_assets', label: '已启用的生产资产' }]} /></Form.Item>
    </> : null}
    {category === 'tasks' && tab === 'queue' ? <>
      {numberField('max_concurrency', '诊断任务并发', 1, 32)}
      {numberField('execution_timeout_seconds', '诊断与扫描队列超时（秒）', 60, 86400)}
      <Form.Item name="failure_policy" label="诊断重试与后续资产"><Select options={[{ value: 'continue', label: '允许诊断重试，继续后续资产' }, { value: 'stop', label: '不再重试，停止后续资产' }]} /></Form.Item>
      <Form.Item name="require_confirmation" label="执行前人工确认" valuePropName="checked"><Switch disabled /></Form.Item>
    </> : null}
    {category === 'tasks' && tab === 'terminal' ? <>
      {numberField('terminal_max_connections', 'SSH 最大并发', 1, 100)}
      {numberField('terminal_idle_timeout_seconds', 'SSH 空闲超时（秒）', 60, 86400)}
      {numberField('terminal_max_duration_seconds', 'SSH 最长会话（秒）', 300, 86400)}
    </> : null}
    {category === 'notifications' && tab === 'delivery' ? <>
      <Form.Item name="enabled" label="启用平台通知" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="default_channel" label="默认渠道"><Select options={[{ value: 'email', label: '邮件' }, { value: 'webhook', label: 'Webhook' }, { value: 'both', label: '邮件 + Webhook' }]} /></Form.Item>
      <Form.Item name="quiet_hours_enabled" label="启用免打扰" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="quiet_hours_start" label="免打扰开始" rules={[{ required: true, pattern: /^([01]\d|2[0-3]):[0-5]\d$/, message: '请输入 HH:mm' }]}><Input placeholder="23:00" /></Form.Item>
      <Form.Item name="quiet_hours_end" label="免打扰结束" rules={[{ required: true, pattern: /^([01]\d|2[0-3]):[0-5]\d$/, message: '请输入 HH:mm' }]}><Input placeholder="07:00" /></Form.Item>
      {numberField('retry_count', '投递重试次数', 0, 10)}
    </> : null}
    {category === 'authentication' && tab === 'local' ? <>
      <Form.Item name="local_login_enabled" label="本地登录" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="ldap_enabled" label="LDAP / AD 登录" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="oidc_enabled" label="OIDC 登录" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="new_account_policy" label="新账号接入"><Select options={[{ value: 'admin_only', label: '仅管理员创建并绑定' }, { value: 'invite_only', label: '仅预先邀请并绑定' }]} /></Form.Item>
    </> : null}
    {category === 'ai' && tab === 'context' ? <>
      <Form.Item name="ops_assistant_enabled" label="运维助手" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="vulnerability_assistant_enabled" label="漏洞安全助手" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="external_vulnerability_enabled" label="允许外部漏洞研判" valuePropName="checked"><Switch aria-label="允许外部漏洞研判" /></Form.Item>
      <Form.Item name="include_terminal_output" label="包含终端输出" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="include_asset_context" label="包含资产上下文" valuePropName="checked"><Switch /></Form.Item>
      {numberField('history_messages', '历史消息数量', 0, 20)}
      <Form.Item name="redact_sensitive_data" label="敏感信息脱敏" valuePropName="checked"><Switch disabled /></Form.Item>
    </> : null}
    {category === 'storage' && tab === 'cleanup' ? <>
      {numberField('retention_days', '监控历史保留（天）', 7, 3650)}
      <Form.Item name="auto_cleanup_enabled" label="自动清理" valuePropName="checked"><Switch /></Form.Item>
      {numberField('cleanup_interval_hours', '清理周期（小时）', 1, 720)}
    </> : null}
    {category === 'storage' && tab === 'backup' ? <>
      <Form.Item name="backup_enabled" label="启用备份" valuePropName="checked"><Switch /></Form.Item>
      {numberField('backup_interval_hours', '备份周期（小时）', 1, 720)}
      <Form.Item name="backup_path" label="备份目录" rules={[{ required: true, validator: (_, value: string | undefined) =>
        value?.startsWith('/var/lib/linux-ops/') && value.slice('/var/lib/linux-ops/'.length).split('/').some(Boolean)
          && !value.split('/').includes('..')
          ? Promise.resolve() : Promise.reject(new Error('备份目录必须位于 /var/lib/linux-ops 下')),
      }]}><Input maxLength={255} /></Form.Item>
      <Form.Item name="verify_backup_after_create" label="备份后验证恢复" valuePropName="checked"><Switch disabled /></Form.Item>
    </> : null}
  </div>;
}
