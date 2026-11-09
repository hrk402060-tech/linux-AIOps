import { useState } from 'react';
import { Alert, Button, Form, Input, message } from 'antd';
import { KeyRound, ShieldCheck } from 'lucide-react';

import { changePassword, type AuthUser } from '../api/auth';
import { PASSWORD_POLICY_MESSAGE, validatePassword } from '../passwordPolicy';

interface PasswordValues {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export function PasswordChangeGate({ onChanged }: {
  onChanged: (user: AuthUser) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [messages, holder] = message.useMessage();
  const submit = async (values: PasswordValues) => {
    setLoading(true);
    try {
      onChanged(await changePassword(values.currentPassword, values.newPassword));
    } catch (error) {
      messages.error(error instanceof Error ? error.message : '密码修改失败');
    } finally {
      setLoading(false);
    }
  };
  return <main className="access-gate">{holder}<section className="access-panel">
    <span className="access-mark"><ShieldCheck size={26} /></span>
    <p>账号安全</p><h1>首次登录修改密码</h1>
    <Alert type="warning" showIcon message="当前密码为临时密码，修改后才能进入控制台。" />
    <Form<PasswordValues> layout="vertical" onFinish={(values) => void submit(values)} style={{ marginTop: 18 }}>
      <Form.Item name="currentPassword" label="当前密码" rules={[{ required: true, min: 8, message: '请输入当前密码' }]}>
        <Input.Password prefix={<KeyRound size={16} />} autoComplete="current-password" />
      </Form.Item>
      <Form.Item name="newPassword" label="新密码" extra={PASSWORD_POLICY_MESSAGE} rules={[
        { required: true, message: PASSWORD_POLICY_MESSAGE },
        { validator: validatePassword },
      ]}>
        <Input.Password prefix={<KeyRound size={16} />} autoComplete="new-password" />
      </Form.Item>
      <Form.Item name="confirmPassword" label="确认新密码" dependencies={['newPassword']} rules={[
        { required: true },
        ({ getFieldValue }) => ({ validator: (_, value) => value === getFieldValue('newPassword') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) }),
      ]}>
        <Input.Password prefix={<KeyRound size={16} />} autoComplete="new-password" />
      </Form.Item>
      <Button block type="primary" htmlType="submit" loading={loading}>修改密码并进入</Button>
    </Form>
  </section></main>;
}
