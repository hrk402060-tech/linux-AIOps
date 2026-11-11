import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, InputNumber, Select, Space, Spin, Switch, Tag, message } from 'antd';
import { RefreshCw, Save } from 'lucide-react';

import { getAuthPolicy, saveAuthPolicy, type AuthPolicy } from '../api/system';

type AuthPolicyForm = Pick<AuthPolicy, 'login_failure_threshold' | 'lock_duration_seconds' | 'session_ttl_seconds' | 'mfa_enforcement' | 'allow_user_mfa_reset'>;

function policyFields(policy: AuthPolicyForm): AuthPolicyForm {
  return {
    login_failure_threshold: policy.login_failure_threshold,
    lock_duration_seconds: policy.lock_duration_seconds,
    session_ttl_seconds: policy.session_ttl_seconds,
    mfa_enforcement: policy.mfa_enforcement,
    allow_user_mfa_reset: policy.allow_user_mfa_reset,
  };
}

function formatDuration(seconds: number) {
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

export function AuthPolicyPanel() {
  const [form] = Form.useForm<AuthPolicyForm>();
  const [policy, setPolicy] = useState<AuthPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [messages, holder] = message.useMessage();
  const mounted = useRef(true);
  const loadingRef = useRef(true);
  const savingRef = useRef(false);
  const loadSequence = useRef(0);

  const load = async () => {
    if (savingRef.current) return;
    const sequence = ++loadSequence.current;
    loadingRef.current = true;
    setLoading(true);
    setPolicy(null);
    setError('');
    form.resetFields();
    try {
      const result = await getAuthPolicy();
      if (!mounted.current || sequence !== loadSequence.current) return;
      setPolicy(result);
      form.setFieldsValue(policyFields(result));
    } catch (cause) {
      if (mounted.current && sequence === loadSequence.current) {
        setError(cause instanceof Error ? cause.message : '登录安全策略加载失败');
      }
    } finally {
      if (mounted.current && sequence === loadSequence.current) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; loadSequence.current += 1; };
  }, []);

  const save = async (values: AuthPolicyForm) => {
    if (!policy || loadingRef.current || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    try {
      const result = await saveAuthPolicy(policyFields(values));
      if (!mounted.current) return;
      setPolicy(result);
      form.setFieldsValue(policyFields(result));
      messages.success('登录安全策略已保存');
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '登录安全策略保存失败');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  return <section className="user-management" aria-label="登录安全策略">
    {holder}
    <div className="panel-heading">
      <div><strong>登录安全策略</strong><span>失败锁定、会话时长和 MFA 要求</span></div>
      <Button aria-label="重新加载登录安全策略" icon={<RefreshCw size={16} />} loading={loading} disabled={saving} onClick={() => void load()} />
    </div>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    <Spin spinning={loading}>
      <Form<AuthPolicyForm>
        form={form}
        layout="vertical"
        disabled={loading || saving || !policy}
        onFinish={(values) => void save(values)}
        style={{ maxWidth: 760, marginTop: 16 }}
      >
        <Space wrap align="start">
          <Form.Item name="login_failure_threshold" label="连续失败次数" rules={[{ required: true, type: 'number', min: 3, max: 20 }]}>
            <InputNumber min={3} max={20} precision={0} />
          </Form.Item>
          <Form.Item name="lock_duration_seconds" label="锁定时长（秒）" rules={[{ required: true, type: 'number', min: 60, max: 86400 }]}>
            <InputNumber min={60} max={86400} precision={0} />
          </Form.Item>
          <Form.Item name="session_ttl_seconds" label="会话有效期（秒）" rules={[{ required: true, type: 'number', min: 900, max: 604800 }]}>
            <InputNumber min={900} max={604800} precision={0} />
          </Form.Item>
        </Space>
        <Form.Item name="mfa_enforcement" label="MFA 强制范围">
          <Select aria-label="MFA 强制范围" options={[
            { value: 'off', label: '不强制（用户可自行启用）' },
            { value: 'admins', label: '仅管理员强制' },
            { value: 'all', label: '所有启用用户强制' },
          ]} />
        </Form.Item>
        <Form.Item name="allow_user_mfa_reset" label="允许用户自行关闭 MFA" valuePropName="checked">
          <Switch aria-label="允许用户自行关闭 MFA" />
        </Form.Item>
        <Alert
          type="info"
          showIcon
          message="用户可在“个人安全”中扫码绑定 MFA；强制策略只影响后续登录。"
          description={<>
            <div>新策略仅适用于后续登录；已有会话需在“活动会话”或“撤销全部会话”中撤销后重新认证。</div>
            <div>未绑定用户下次登录会直接进入二维码绑定流程，绑定完成后才能进入平台。</div>
            {policy ? <div>当前来源：{policy.source}；锁定 {formatDuration(policy.lock_duration_seconds)}；会话 {formatDuration(policy.session_ttl_seconds)}。</div> : null}
          </>}
        />
        <Space wrap style={{ marginTop: 16 }}>
          {policy ? <Tag>{policy.updated_at ? `最后更新：${new Date(policy.updated_at).toLocaleString('zh-CN')}` : '使用环境默认值'}</Tag> : null}
          <Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={saving} disabled={loading || saving || !policy}>保存登录策略</Button>
        </Space>
      </Form>
    </Spin>
  </section>;
}
