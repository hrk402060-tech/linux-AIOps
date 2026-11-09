import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, Space, Switch, Tag } from 'antd';
import { RefreshCw, Save } from 'lucide-react';
import { getTerminalAISettings, isValidTerminalAIUrl, saveTerminalAISettings, type TerminalAISettings } from '../api/ai-settings';
import { AIProvidersPanel } from './AIProvidersPanel';

interface TerminalAIForm {
  enabled: boolean; base_url: string; model: string; timeout_seconds: number;
  api_key: string; clear_api_key: boolean;
}
const emptyForm: TerminalAIForm = { enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key: '', clear_api_key: false };
function toForm(settings: TerminalAISettings): TerminalAIForm {
  return { ...emptyForm, enabled: settings.enabled, base_url: settings.base_url ?? '', model: settings.model ?? '', timeout_seconds: settings.timeout_seconds };
}
export function AISettingsPanel() {
  const [form] = Form.useForm<TerminalAIForm>();
  const [settings, setSettings] = useState<TerminalAISettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const mounted = useRef(true);
  const savingRef = useRef(false);
  const loadSequence = useRef(0);
  const key = Form.useWatch('api_key', form) as string | undefined;
  const clearKey = Form.useWatch('clear_api_key', form) as boolean | undefined;
  const load = async () => {
    if (savingRef.current) return;
    const sequence = ++loadSequence.current;
    setLoading(true); setSettings(null); setError(''); setSuccess(false);
    form.setFieldsValue(emptyForm);
    try {
      const result = await getTerminalAISettings();
      if (!mounted.current || sequence !== loadSequence.current) return;
      setSettings(result); form.setFieldsValue(toForm(result));
    } catch (cause) {
      if (mounted.current && sequence === loadSequence.current) setError(cause instanceof Error ? cause.message : '运维 AI 配置加载失败');
    } finally { if (mounted.current && sequence === loadSequence.current) setLoading(false); }
  };
  useEffect(() => {
    mounted.current = true; void load();
    return () => { mounted.current = false; loadSequence.current += 1; };
  }, []);
  const save = async (values: TerminalAIForm) => {
    if (!settings || loading || savingRef.current) return;
    if (values.clear_api_key && values.api_key?.trim()) { setError('清除密钥与填写新密钥不能同时选择。'); return; }
    const normalizeUrl = (value: string) => value.trim().replace(/\/+$/, '');
    if (settings.api_key_masked && normalizeUrl(values.base_url ?? '') !== normalizeUrl(settings.base_url)
        && !values.api_key?.trim() && !values.clear_api_key) {
      setError('更换模型地址时请重新填写密钥或选择清除已保存密钥。'); return;
    }
    savingRef.current = true; setSaving(true); setError(''); setSuccess(false);
    try {
      const result = await saveTerminalAISettings({
        enabled: values.enabled, base_url: values.base_url?.trim() ?? '',
        model: values.model?.trim() ?? '', timeout_seconds: values.timeout_seconds,
        api_key: values.api_key?.trim() || null, clear_api_key: values.clear_api_key,
      });
      if (!mounted.current) return;
      setSettings(result); form.setFieldsValue(toForm(result)); setSuccess(true);
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '运维 AI 配置保存失败');
    } finally { savingRef.current = false; if (mounted.current) setSaving(false); }
  };
  const unavailable = loading || saving || !settings;
  return <section className="ai-settings" aria-label="AI 配置">
    <section className="user-management" aria-label="运维 AI 配置">
      <div className="panel-heading"><div><strong>运维 AI 配置</strong><span>SSH 对话模型</span></div><Button aria-label="重新加载运维配置" icon={<RefreshCw size={16} />} loading={loading} disabled={saving} onClick={() => void load()} /></div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {success ? <Alert type="success" showIcon message="运维配置已保存，下次对话立即生效。" /> : null}
      <Form form={form} layout="vertical" style={{ maxWidth: 760 }} initialValues={emptyForm} disabled={unavailable} onFinish={(values) => void save(values)}>
        <Form.Item name="enabled" label="启用外部运维模型" valuePropName="checked"><Switch aria-label="启用外部运维模型" /></Form.Item>
        <Form.Item name="base_url" label="模型接口地址" dependencies={['enabled']} rules={[{ validator: (_, value: string | undefined) => {
          const url = value?.trim() ?? '';
          if (!url && !form.getFieldValue('enabled')) return Promise.resolve();
          return isValidTerminalAIUrl(url) ? Promise.resolve() : Promise.reject(new Error('请输入 HTTP(S) 地址，不得包含用户名、密码、查询参数或片段。'));
        } }]}><Input placeholder="https://model.example/v1" autoComplete="off" maxLength={1000} /></Form.Item>
        <Form.Item name="model" label="模型名称" dependencies={['enabled']} rules={[{ validator: (_, value: string | undefined) => !form.getFieldValue('enabled') || value?.trim() ? Promise.resolve() : Promise.reject(new Error('启用时必须填写模型名称。')) }]}><Input autoComplete="off" maxLength={200} /></Form.Item>
        <Form.Item name="timeout_seconds" label="请求超时（秒）" rules={[{ validator: (_, value: number | null) => Number.isInteger(value) && value !== null && value >= 5 && value <= 120 ? Promise.resolve() : Promise.reject(new Error('超时必须为 5 至 120 秒的整数。')) }]}><InputNumber min={5} max={120} precision={0} changeOnBlur={false} /></Form.Item>
        <Form.Item name="api_key" label="运维 API Key" extra="留空保留已保存密钥；更换地址需重新填写或清除密钥。" rules={[{ validator: (_, value: string | undefined) => !value?.trim() || (value.trim().length >= 8 && value.trim().length <= 400 && !/[\u0000-\u001f\u007f]/.test(value)) ? Promise.resolve() : Promise.reject(new Error('API Key 需 8 至 400 个字符且不含控制字符。')) }]}><Input.Password autoComplete="new-password" disabled={unavailable || clearKey} maxLength={400} /></Form.Item>
        <Form.Item name="clear_api_key" valuePropName="checked"><Checkbox disabled={unavailable || Boolean(key?.trim())}>清除已保存密钥</Checkbox></Form.Item>
        <Space wrap><Tag>{settings?.api_key_masked ?? '未保存密钥'}</Tag><span>{settings?.updated_at ? `最后保存：${new Date(settings.updated_at).toLocaleString('zh-CN')}` : '尚未保存运维配置'}</span><Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={saving} disabled={unavailable}>保存运维配置</Button></Space>
      </Form>
    </section>
    <AIProvidersPanel />
  </section>;
}
