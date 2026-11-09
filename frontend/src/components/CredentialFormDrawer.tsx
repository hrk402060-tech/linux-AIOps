import { useEffect } from 'react';
import { Button, Drawer, Form, Input, Segmented, Space } from 'antd';
import { KeyRound, LockKeyhole } from 'lucide-react';

import type { Credential, CredentialCreate, CredentialType, CredentialUpdate } from '../types/credential';

interface CredentialFormValues {
  name: string;
  credential_type: CredentialType;
  username: string;
  secret?: string;
}

interface CredentialFormDrawerProps {
  open: boolean;
  credential: Credential | null;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (payload: CredentialCreate | CredentialUpdate) => Promise<void>;
}

export function CredentialFormDrawer(props: CredentialFormDrawerProps) {
  const { open, credential, submitting, onClose, onSubmit } = props;
  const [form] = Form.useForm<CredentialFormValues>();
  const type = Form.useWatch('credential_type', form) ?? 'password';

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue(
      credential
        ? { name: credential.name, credential_type: credential.credential_type, username: credential.username }
        : { credential_type: 'password' },
    );
  }, [credential, form, open]);

  const submit = async (values: CredentialFormValues) => {
    if (credential) {
      const payload: CredentialUpdate = { name: values.name, username: values.username };
      if (values.secret) payload.secret = values.secret;
      await onSubmit(payload);
    } else {
      await onSubmit({ ...values, secret: values.secret ?? '' });
    }
    form.resetFields();
  };

  return (
    <Drawer
      title={credential ? '编辑凭据' : '新建凭据'}
      open={open}
      onClose={() => { form.resetFields(); onClose(); }}
      width={480}
      destroyOnClose
      footer={
        <div className="drawer-footer">
          <span className="encryption-label"><LockKeyhole size={15} />AES-256-GCM</span>
          <Space><Button onClick={onClose}>取消</Button><Button type="primary" loading={submitting} onClick={() => form.submit()}>保存</Button></Space>
        </div>
      }
    >
      <Form form={form} layout="vertical" onFinish={submit} requiredMark="optional">
        <Form.Item name="name" label="凭据名称" rules={[{ required: true, whitespace: true, message: '请输入凭据名称' }, { min: 2, max: 100 }]}>
          <Input placeholder="例如：生产环境只读账号" autoComplete="off" />
        </Form.Item>
        <Form.Item name="credential_type" label="认证方式" rules={[{ required: true }]}>
          <Segmented
            block
            disabled={Boolean(credential)}
            options={[{ label: '密码', value: 'password' }, { label: 'SSH 私钥', value: 'private_key' }]}
          />
        </Form.Item>
        <Form.Item name="username" label="登录用户" rules={[{ required: true, whitespace: true, message: '请输入登录用户' }, { max: 64 }]}>
          <Input placeholder="例如：ops-reader" autoComplete="username" />
        </Form.Item>
        <Form.Item
          name="secret"
          label={type === 'private_key' ? 'SSH 私钥' : '登录密码'}
          rules={[{ required: !credential, message: type === 'private_key' ? '请输入 SSH 私钥' : '请输入登录密码' }]}
          extra={credential ? '留空则保持现有密钥不变' : undefined}
        >
          {type === 'private_key' ? (
            <Input.TextArea rows={9} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" autoComplete="off" />
          ) : (
            <Input.Password prefix={<KeyRound size={16} />} placeholder={credential ? '输入新密码以替换' : '输入登录密码'} autoComplete="new-password" />
          )}
        </Form.Item>
      </Form>
    </Drawer>
  );
}
