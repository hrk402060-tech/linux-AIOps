import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Select, Space, Switch, Tag } from 'antd';
import { FlaskConical, Save } from 'lucide-react';
import { getIdentitySource, saveIdentitySource, testIdentitySource, type IdentityProvider, type IdentitySource, type LdapSource, type OidcSource, type IdentityTest } from '../../api/identitySources';
import { formatPlatformDateTime } from '../../utils/platformPreferences';

const ldapDefaults: LdapSource = {
  server_url: '', base_dn: '', bind_dn: '', user_filter: '(uid={username})', username_attribute: 'uid',
  subject_attribute: 'entryUUID', display_name_attribute: 'displayName', timeout_seconds: 10, start_tls: true,
};
const oidcDefaults: OidcSource = {
  issuer_url: '', client_id: '', redirect_uri: `${window.location.origin}/api/v1/auth/identity/oidc/callback`,
  scopes: ['openid', 'profile', 'email'], timeout_seconds: 10,
};
export function IdentitySettingsPanel({ provider }: { provider: IdentityProvider }) {
  return <IdentitySourceForm key={provider} provider={provider} />;
}
function IdentitySourceForm({ provider }: { provider: IdentityProvider }) {
  const [form] = Form.useForm<LdapSource & OidcSource>();
  const [source, setSource] = useState<IdentitySource | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState<IdentityTest | null>(null);
  const generation = useRef(0);
  const mutation = useRef(false);
  const fields = (value: IdentitySource | LdapSource | OidcSource): LdapSource | OidcSource => provider === 'ldap'
    ? Object.fromEntries(Object.keys(ldapDefaults).map((key) => [key, value[key as keyof typeof value]])) as unknown as LdapSource
    : Object.fromEntries(Object.keys(oidcDefaults).map((key) => [key, value[key as keyof typeof value]])) as unknown as OidcSource;
  useEffect(() => {
    const request = ++generation.current;
    setLoading(true);
    getIdentitySource(provider).then((next) => {
      if (request !== generation.current) return;
      setSource(next); form.setFieldsValue({ ...fields(next), bind_password: '', client_secret: '' });
    }).catch((cause: unknown) => {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : '身份源配置加载失败');
    }).finally(() => { if (request === generation.current) setLoading(false); });
    return () => { generation.current += 1; };
  }, [provider, form]);
  const run = async (test: boolean) => {
    if (!source || mutation.current) return;
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const payload = { ...fields(values), ...(provider === 'ldap'
      ? values.bind_password ? { bind_password: values.bind_password } : {}
      : values.client_secret ? { client_secret: values.client_secret } : {}) };
    const request = generation.current;
    mutation.current = true; setWorking(true); setError(''); setNotice(''); setResult(null);
    try {
      if (test) {
        const next = await testIdentitySource(provider, payload);
        if (request === generation.current) setResult(next);
      } else {
        const next = await saveIdentitySource(provider, payload, source.revision);
        if (request !== generation.current) return;
        setSource(next); form.setFieldsValue({ bind_password: '', client_secret: '' }); setNotice('身份源连接已保存');
      }
    } catch (cause) {
      if (request === generation.current) setError(cause instanceof Error ? cause.message : '身份源操作失败');
    } finally { mutation.current = false; if (request === generation.current) setWorking(false); }
  };
  const required = [{ required: true, whitespace: true, message: '此项必填' }];
  return <section aria-label={`${provider === 'ldap' ? 'LDAP / AD' : 'OIDC'} 身份源`}>
    {error ? <Alert showIcon type="error" message={error} /> : null}
    {notice ? <Alert showIcon type="success" message={notice} /> : null}
    {result ? <Alert showIcon type={result.success ? 'success' : 'error'} message={result.message}
      description={`${formatPlatformDateTime(result.checked_at)} · ${result.latency_ms} ms`} /> : null}
    <Form form={form} initialValues={provider === 'ldap' ? ldapDefaults : oidcDefaults} disabled={loading || working || !source}
      layout="vertical" className="platform-settings-form" onFinish={() => void run(false)}>
      <div className="settings-form-grid">
        {provider === 'ldap' ? <>
          <Form.Item name="server_url" label="LDAP 地址" rules={[...required, { pattern: /^ldaps?:\/\//, message: '请使用 ldap:// 或 ldaps:// 地址' }]}><Input placeholder="ldaps://directory.example:636" /></Form.Item>
          <Form.Item name="start_tls" label="LDAP StartTLS" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="base_dn" label="查询根 DN" rules={required}><Input /></Form.Item>
          <Form.Item name="bind_dn" label="绑定账号 DN" rules={required}><Input autoComplete="off" /></Form.Item>
          <Form.Item name="bind_password" label="绑定密码"><Input.Password autoComplete="new-password" placeholder={source?.secret_configured ? '留空保留已保存密码' : '未保存密码'} /></Form.Item>
          <Form.Item name="user_filter" label="用户过滤器" rules={required}><Input /></Form.Item>
          <Form.Item name="username_attribute" label="用户名属性" rules={required}><Input /></Form.Item>
          <Form.Item name="subject_attribute" label="唯一身份属性" rules={required}><Input /></Form.Item>
          <Form.Item name="display_name_attribute" label="显示名称属性" rules={required}><Input /></Form.Item>
        </> : <>
          <Form.Item name="issuer_url" label="Issuer 地址" rules={[...required, { type: 'url' }]}><Input placeholder="https://identity.example" /></Form.Item>
          <Form.Item name="client_id" label="Client ID" rules={required}><Input /></Form.Item>
          <Form.Item name="client_secret" label="Client Secret"><Input.Password autoComplete="new-password" placeholder={source?.secret_configured ? '留空保留已保存密钥' : '未保存密钥'} /></Form.Item>
          <Form.Item name="redirect_uri" label="登录回调地址" rules={[...required, { type: 'url' }]}><Input /></Form.Item>
          <Form.Item name="scopes" label="请求范围" rules={[{ required: true }]}><Select mode="tags" options={['openid', 'profile', 'email'].map((value) => ({ value }))} /></Form.Item>
        </>}
        <Form.Item name="timeout_seconds" label="连接超时（秒）" rules={[{ required: true, type: 'integer', min: 3, max: 60 }]}><InputNumber min={3} max={60} precision={0} /></Form.Item>
      </div>
      <Space wrap className="platform-settings-actions">
        <Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={working}>保存连接</Button>
        <Button icon={<FlaskConical size={16} />} loading={working} onClick={() => void run(true)}>测试当前配置</Button>
        <Tag>{source?.secret_configured ? '密钥已保存' : '密钥未配置'}</Tag>
        {source?.tested_at ? <Tag color={source.test_success ? 'success' : 'error'}>上次测试：{source.test_success ? '通过' : '失败'}</Tag> : null}
      </Space>
    </Form>
  </section>;
}
