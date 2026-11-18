import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Form, Input, Popconfirm, Select, Table, Tag } from 'antd';
import { Link2, Unlink, UserPlus } from 'lucide-react';
import { getUsers, type AuthUser } from '../../api/auth';
import { createIdentityBinding, deleteIdentityBinding, getIdentityBindings, type IdentityBinding, type IdentityProvider } from '../../api/identitySources';
import { IdentityInvitationModal } from './IdentityInvitationModal';
import { formatPlatformDateTime } from '../../utils/platformPreferences';

export function IdentityBindingsPanel() {
  const [provider, setProvider] = useState<IdentityProvider>('ldap');
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [bindings, setBindings] = useState<IdentityBinding[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [form] = Form.useForm<{ subject: string; user_id: string }>();
  const generation = useRef(0);
  const mutation = useRef(false);
  useEffect(() => {
    const sequence = ++generation.current;
    setLoading(true); setError(''); setBindings([]);
    Promise.all([getUsers(), getIdentityBindings(provider)]).then(([nextUsers, next]) => {
      if (sequence === generation.current) { setUsers(nextUsers); setBindings(next); }
    }).catch((cause: unknown) => {
      if (sequence === generation.current) setError(cause instanceof Error ? cause.message : '身份绑定加载失败');
    }).finally(() => { if (sequence === generation.current) setLoading(false); });
    return () => { generation.current += 1; };
  }, [provider]);
  const bind = async ({ subject, user_id }: { subject: string; user_id: string }) => {
    if (mutation.current) return;
    mutation.current = true; setWorking(true); setError('');
    const sequence = generation.current;
    try {
      const next = await createIdentityBinding(provider, subject.trim(), user_id);
      if (sequence !== generation.current) return;
      setBindings((current) => [...current.filter((item) => item.subject !== next.subject), next]);
      form.resetFields();
    } catch (cause) {
      if (sequence === generation.current) setError(cause instanceof Error ? cause.message : '身份绑定保存失败');
    } finally { mutation.current = false; if (sequence === generation.current) setWorking(false); }
  };
  const unbind = async (subject: string) => {
    if (mutation.current || loading) return;
    mutation.current = true; setWorking(true); setError('');
    const sequence = generation.current;
    try {
      await deleteIdentityBinding(provider, subject);
      if (sequence === generation.current) setBindings((current) => current.filter((item) => item.subject !== subject));
    } catch (cause) {
      if (sequence === generation.current) setError(cause instanceof Error ? cause.message : '身份绑定解除失败');
    } finally { mutation.current = false; if (sequence === generation.current) setWorking(false); }
  };
  return <section className="settings-section" aria-label="外部身份授权">
    <h2>外部身份授权</h2>
    {error ? <Alert showIcon type="error" message={error} /> : null}
    <Select aria-label="身份源类型" value={provider} disabled={working} style={{ width: 180, marginBottom: 16 }}
      onChange={(next) => { setProvider(next); form.resetFields(); }} options={[{ value: 'ldap', label: 'LDAP / AD' }, { value: 'oidc', label: 'OIDC' }]} />
    <Form name="identity-binding" form={form} layout="vertical" disabled={loading || working} onFinish={(values) => void bind(values)}>
      <div className="settings-form-grid">
        <Form.Item name="subject" label="外部唯一身份" rules={[{ required: true, whitespace: true, max: 255 }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="user_id" label="平台用户" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={
          users.filter((user) => user.enabled).map((user) => ({ value: user.id, label: `${user.display_name || user.username} (${user.username})` }))
        } /></Form.Item>
      </div>
      <Button type="primary" htmlType="submit" icon={<Link2 size={16} />} loading={working}>绑定身份</Button>
      <Button icon={<UserPlus size={16} />} onClick={() => setInviting(true)} style={{ marginLeft: 8 }}>邀请外部账号</Button>
    </Form>
    <Table<IdentityBinding> rowKey={(item) => item.id ?? item.subject} loading={loading} dataSource={bindings}
      size="small" style={{ marginTop: 18 }} columns={[
        { title: '外部唯一身份', dataIndex: 'subject', ellipsis: true },
        { title: '平台用户', dataIndex: 'user_id', render: (id: string | null, item) => users.find((user) => user.id === id)?.username ?? item.username ?? id },
        { title: '状态', render: (_, item) => item.kind === 'invited'
          ? <span><Tag>待接入</Tag><small>有效至 {formatPlatformDateTime(item.expires_at)}</small></span> : <Tag>已绑定</Tag> },
        { title: '操作', width: 130, render: (_, item) => <Popconfirm title="解除身份绑定？" okText="确定" cancelText="取消"
          disabled={working} onConfirm={() => unbind(item.subject)}>
          <Button danger size="small" disabled={working} icon={<Unlink size={14} />}>解除绑定</Button>
        </Popconfirm> },
      ]} />
    {inviting ? <IdentityInvitationModal provider={provider} onClose={() => setInviting(false)}
      onSaved={(next) => setBindings((current) => [next, ...current.filter((item) => item.subject !== next.subject)])} /> : null}
  </section>;
}
