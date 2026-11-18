import { useRef, useState } from 'react';
import { Alert, Button, Form, Input, InputNumber, Modal, Select } from 'antd';
import { inviteIdentityUser, type IdentityBinding, type IdentityInvitation, type IdentityProvider } from '../../api/identitySources';

export function IdentityInvitationModal({ provider, onClose, onSaved }: {
  provider: IdentityProvider; onClose: () => void; onSaved: (binding: IdentityBinding) => void;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const mutation = useRef(false);
  const submit = async (values: IdentityInvitation) => {
    if (mutation.current) return;
    mutation.current = true; setWorking(true); setError('');
    try {
      const result = await inviteIdentityUser(provider, {
        ...values, subject: values.subject.trim(), username: values.username.trim(),
      });
      onSaved(result); onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '外部账号邀请失败');
    } finally { mutation.current = false; setWorking(false); }
  };
  return <Modal title="邀请外部账号" open footer={null} onCancel={onClose}
    closable={!working} maskClosable={!working} keyboard={!working}>
    {error ? <Alert showIcon type="error" message={error} /> : null}
    <Form<IdentityInvitation> name="identity-invitation" layout="vertical" disabled={working}
      initialValues={{ role: 'auditor', expires_hours: 24 }} onFinish={(values) => void submit(values)}>
      <Form.Item name="subject" label="外部唯一身份" rules={[{ required: true, whitespace: true, max: 255 }]}><Input autoComplete="off" /></Form.Item>
      <Form.Item name="username" label="平台用户名" rules={[{ required: true, min: 3, max: 64, pattern: /^[A-Za-z0-9._-]+$/ }]}><Input autoComplete="off" /></Form.Item>
      <Form.Item name="display_name" label="显示名称" rules={[{ max: 120 }]}><Input /></Form.Item>
      <Form.Item name="role" label="角色" rules={[{ required: true }]}><Select options={[
        { value: 'auditor', label: '审计员' }, { value: 'operator', label: '操作员' },
      ]} /></Form.Item>
      <Form.Item name="expires_hours" label="邀请有效期（小时）" rules={[{ required: true, type: 'integer', min: 1, max: 168 }]}>
        <InputNumber min={1} max={168} precision={0} />
      </Form.Item>
      <Button type="primary" htmlType="submit" loading={working}>创建邀请</Button>
    </Form>
  </Modal>;
}
