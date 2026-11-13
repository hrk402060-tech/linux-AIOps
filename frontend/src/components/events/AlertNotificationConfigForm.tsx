import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Descriptions, Form, Input, InputNumber, Select, Switch, Tag, message } from 'antd';
import { Save } from 'lucide-react';
import {
  saveAlertNotificationConfig, type AlertNotificationConfig, type AlertNotificationConfigInput,
} from '../../api/alertNotifications';

interface ConfigValues {
  enabled: boolean; host: string; port: number; security: 'starttls' | 'ssl';
  username: string; sender: string; recipients: string; password?: string;
}
const emailPattern = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$/;
const headerSafe = (value: string) => !/[\x00-\x1f\x7f]/.test(value);
const validAddress = (value: string) => value.length <= 254 && !value.includes('..') && emailPattern.test(value);
const validHost = (value: string) => {
  const host = value.trim();
  if (!headerSafe(value)) return false;
  if (!host.includes(':')) return /^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/.test(host);
  try { return new URL(`http://[${host}]/`).hostname.length > 2; } catch { return false; }
};
const addresses = (value: string) => value.split(/[\n,;]/).map((item) => item.trim()).filter(Boolean);

export function AlertNotificationConfigForm({ config, writable, onSaved }: {
  config: AlertNotificationConfig; writable: boolean; onSaved: (next: AlertNotificationConfig) => void;
}) {
  return writable ? <EditableConfigForm config={config} onSaved={onSaved} /> : <ReadOnlyConfig config={config} />;
}

function EditableConfigForm({ config, onSaved }: {
  config: AlertNotificationConfig; onSaved: (next: AlertNotificationConfig) => void;
}) {
  const [form] = Form.useForm<ConfigValues>();
  const enabled = Form.useWatch('enabled', form) ?? config.enabled;
  const username = Form.useWatch('username', form) ?? config.username;
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const [messages, holder] = message.useMessage();
  useEffect(() => { form.setFieldsValue({ ...config, recipients: config.recipients.join('\n'), password: '' }); }, [config, form]);
  const save = async (values: ConfigValues) => {
    if (inFlight.current) return;
    inFlight.current = true; setSaving(true);
    const payload: AlertNotificationConfigInput = {
      enabled: values.enabled, port: values.port, security: values.security,
      username: values.username?.trim() ?? '',
      ...(values.host?.trim() ? { host: values.host.trim() } : {}),
      ...(values.sender?.trim() ? { sender: values.sender.trim() } : {}),
      ...(addresses(values.recipients ?? '').length ? { recipients: addresses(values.recipients) } : {}),
      ...(values.password ? { password: values.password } : {}),
    };
    try {
      const next = await saveAlertNotificationConfig(payload);
      form.setFieldValue('password', ''); onSaved(next);
      messages.success(next.enabled ? '通知配置已保存' : '通知通道已停用，历史记录保留');
    } catch (error) { messages.error(error instanceof Error ? error.message : '通知配置保存失败'); }
    finally { inFlight.current = false; setSaving(false); }
  };
  return <section className="notification-config-section">{holder}
    <h3>管理员通知通道 <ChannelState config={config} /></h3>
    <Form form={form} layout="vertical" onFinish={(values) => void save(values)} disabled={saving} className="notification-config-form">
      <Form.Item name="enabled" label="通知通道" valuePropName="checked"><Switch aria-label="通知通道启用状态" checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
      <div className="notification-config-grid">
        <Form.Item name="host" label="SMTP 服务器" rules={[{ required: enabled, whitespace: true, message: '请输入 SMTP 服务器' }, { validator: (_, value: string) => !value || validHost(value) ? Promise.resolve() : Promise.reject(new Error('SMTP 服务器格式不正确')) }]}><Input aria-label="SMTP 服务器" maxLength={253} /></Form.Item>
        <Form.Item name="port" label="端口" rules={[{ required: true, type: 'number', min: 1, max: 65535, message: '端口范围为 1–65535' }]}><InputNumber aria-label="SMTP 端口" min={1} max={65535} precision={0} /></Form.Item>
        <Form.Item name="security" label="加密方式" rules={[{ required: true }]}><Select aria-label="SMTP 加密方式" options={[{ value: 'starttls', label: 'STARTTLS' }, { value: 'ssl', label: 'SSL/TLS' }]} /></Form.Item>
        <Form.Item name="sender" label="发件人" rules={[{ required: enabled, message: '请输入发件人邮箱' }, { validator: (_, value: string) => !value || (headerSafe(value) && validAddress(value.trim())) ? Promise.resolve() : Promise.reject(new Error('发件人邮箱格式不正确')) }]}><Input aria-label="发件人" maxLength={254} /></Form.Item>
        <Form.Item name="username" label="SMTP 用户名" rules={[{ validator: (_, value: string) => !value || headerSafe(value) ? Promise.resolve() : Promise.reject(new Error('用户名不可包含控制字符')) }]}><Input aria-label="SMTP 用户名" autoComplete="off" maxLength={200} /></Form.Item>
        <Form.Item name="password" label="SMTP 密码" rules={[{
          required: enabled && Boolean(username?.trim()) && !config.password_configured,
          message: '填写 SMTP 用户名时必须配置密码',
        }]}><Input.Password aria-label="SMTP 密码" autoComplete="new-password" placeholder={config.password_configured ? '********' : '未配置'} maxLength={4096} /></Form.Item>
      </div>
      <Form.Item name="recipients" label="收件人" rules={[{ required: enabled, whitespace: true, message: '请输入收件人邮箱' }, { validator: (_, value: string) => {
        const items = addresses(value ?? '');
        if (items.length > 20) return Promise.reject(new Error('最多配置 20 个收件人'));
        return items.every((item) => validAddress(item) && headerSafe(item)) ? Promise.resolve() : Promise.reject(new Error('收件人邮箱格式不正确'));
      } }]}><Input.TextArea aria-label="收件人" rows={3} maxLength={5200} /></Form.Item>
      <div className="notification-config-actions"><span>{config.password_configured ? '已保存密码；留空保留原密码' : 'SMTP 密码未配置'}</span><Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={saving}>保存通知配置</Button></div>
    </Form>
    {!config.enabled && config.configured ? <Alert type="warning" showIcon message="通知通道已停用，待发送记录保留" /> : null}
  </section>;
}
function ChannelState({ config }: { config: AlertNotificationConfig }) {
  return <Tag color={config.configured && config.enabled ? 'green' : 'gold'}>{!config.configured ? '未配置' : config.enabled ? '已启用' : '已停用'}</Tag>;
}

function ReadOnlyConfig({ config }: { config: AlertNotificationConfig }) {
  return <section className="notification-config-section">
    <h3>管理员通知通道 <ChannelState config={config} /></h3>
    <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
      { key: 'host', label: 'SMTP 服务器', children: config.host || '未配置' },
      { key: 'port', label: '端口 / TLS', children: `${config.port} / ${config.security === 'ssl' ? 'SSL/TLS' : 'STARTTLS'}` },
      { key: 'sender', label: '发件人', children: config.sender || '未配置' },
      { key: 'recipients', label: '收件人', children: config.recipients.join('、') || '未配置' },
      { key: 'auth', label: '认证', children: config.password_configured ? '密码已配置' : '未配置密码' },
      { key: 'updated', label: '更新时间', children: config.updated_at ? new Date(config.updated_at).toLocaleString() : '-' },
    ]} />
  </section>;
}
