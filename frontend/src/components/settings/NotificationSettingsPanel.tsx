import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, Space, Switch, Tag } from 'antd';
import { FlaskConical, Save } from 'lucide-react';
import { getAlertNotificationConfig, type AlertNotificationConfig } from '../../api/alertNotifications';
import { AlertNotificationConfigForm } from '../events/AlertNotificationConfigForm';
import { getWebhookChannel, saveWebhookChannel, testSettingsChannel, type WebhookChannel, type WebhookChannelInput, type SettingsConnectionTest } from '../../api/settingsNotifications';
import { formatPlatformDateTime } from '../../utils/platformPreferences';
import '../events/AlertNotifications.css';

export function NotificationSettingsPanel({ channel }: { channel: 'email' | 'webhook' }) {
  return <NotificationChannel key={channel} channel={channel} />;
}
function NotificationChannel({ channel }: { channel: 'email' | 'webhook' }) {
  const [smtp, setSmtp] = useState<AlertNotificationConfig | null>(null);
  const [webhook, setWebhook] = useState<WebhookChannel | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState<SettingsConnectionTest | null>(null);
  const [form] = Form.useForm<WebhookChannelInput>();
  const generation = useRef(0);
  const mutation = useRef(false);
  useEffect(() => {
    const request = ++generation.current;
    const load = async () => {
      try {
        if (channel === 'email') {
          const next = await getAlertNotificationConfig();
          if (request === generation.current) setSmtp(next);
        } else {
          const next = await getWebhookChannel();
          if (request !== generation.current) return;
          setWebhook(next); form.setFieldsValue({ enabled: next.enabled, url: '', token: '', clear_token: false });
        }
      } catch (cause) {
        if (request === generation.current) setError(cause instanceof Error ? cause.message : '通知渠道加载失败');
      } finally { if (request === generation.current) setLoading(false); }
    };
    void load();
    return () => { generation.current += 1; };
  }, [channel, form]);
  const save = async (values: WebhookChannelInput) => {
    if (!webhook || mutation.current) return;
    mutation.current = true; setWorking(true); setError(''); setNotice(''); setResult(null);
    const request = generation.current;
    try {
      const next = await saveWebhookChannel({
        enabled: values.enabled, ...(values.url?.trim() ? { url: values.url.trim() } : {}),
        ...(values.token ? { token: values.token } : {}), clear_token: values.clear_token ?? false,
      });
      if (request !== generation.current) return;
      setWebhook(next); form.setFieldsValue({ enabled: next.enabled, url: '', token: '', clear_token: false });
      setNotice('消息渠道已保存');
    } catch (cause) {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : '消息渠道保存失败');
    } finally { mutation.current = false; if (request === generation.current) setWorking(false); }
  };
  const test = async () => {
    if (mutation.current) return;
    mutation.current = true; setWorking(true); setError(''); setResult(null);
    const request = generation.current;
    try {
      const next = await testSettingsChannel(channel);
      if (request === generation.current) setResult(next);
    } catch (cause) {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : '通知测试失败');
    } finally { mutation.current = false; if (request === generation.current) setWorking(false); }
  };
  const enabled = smtp?.enabled ?? webhook?.enabled ?? false;
  return <section aria-label={channel === 'email' ? '邮件配置' : 'Webhook 配置'}>
    {error ? <Alert showIcon type="error" message={error} /> : null}
    {notice ? <Alert showIcon type="success" message={notice} /> : null}
    {result ? <Alert showIcon type={result.ok ? 'success' : 'error'} message={result.detail} description={formatPlatformDateTime(result.checked_at)} /> : null}
    {smtp ? <AlertNotificationConfigForm config={smtp} writable={!working} onSaved={(next) => { setSmtp(next); setResult(null); }} /> : null}
    {webhook ? <Form form={form} layout="vertical" disabled={working} className="platform-settings-form" onFinish={(values) => void save(values)}>
      <Form.Item name="enabled" label="启用消息渠道" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="url" label="Webhook 地址" rules={[{ validator: (_, value?: string) => {
        if (!value?.trim()) return webhook.url_masked || !form.getFieldValue('enabled') ? Promise.resolve() : Promise.reject(new Error('请输入 Webhook 地址'));
        try {
          const url = new URL(value.trim());
          return url.protocol === 'https:' && !url.username && !url.password && !url.hash
            ? Promise.resolve() : Promise.reject(new Error('请输入不含用户信息和片段的 HTTPS 地址'));
        } catch { return Promise.reject(new Error('Webhook 地址无效')); }
      } }]}><Input.Password autoComplete="new-password" placeholder={webhook.url_masked ? '留空保留已保存地址' : 'https://'} /></Form.Item>
      <Form.Item name="token" label="认证密钥"><Input.Password autoComplete="new-password" placeholder={webhook.token_configured ? '留空保留已保存密钥' : '可选'} /></Form.Item>
      <Form.Item name="clear_token" valuePropName="checked"><Checkbox>清除已保存密钥</Checkbox></Form.Item>
      <Space wrap className="platform-settings-actions">
        <Button type="primary" htmlType="submit" loading={working} icon={<Save size={16} />}>保存消息渠道</Button>
        <Tag>{webhook.token_configured ? '密钥已保存' : '未保存认证密钥'}</Tag>
        {webhook.url_masked ? <Tag>{webhook.url_masked}</Tag> : null}
      </Space>
    </Form> : null}
    <div className="settings-section">
      <Button icon={<FlaskConical size={16} />} disabled={loading || working || !enabled} loading={working} onClick={() => void test()}>
        {channel === 'email' ? '发送测试邮件' : '测试已保存渠道'}
      </Button>
    </div>
  </section>;
}
