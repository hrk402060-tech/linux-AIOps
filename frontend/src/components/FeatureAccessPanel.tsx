import { Alert, Button, Popconfirm, Space, Spin, Table, Tabs, Tag, Tooltip } from 'antd';
import { Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { AuthUser } from '../api/auth';
import { getAccessPolicy, saveAccessPolicy, type AccessPolicy, type CustomRole, type UserAssignment, type UserGroup } from '../api/accessPolicy';
import { ApiError } from '../api/client';
import { FeatureAccessEditor, type AccessEdit } from './FeatureAccessEditor';
import './FeatureAccessPanel.css';

function newId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function upsert<T>(items: T[], value: T, matches: (item: T) => boolean): T[] {
  return items.some(matches) ? items.map((item) => matches(item) ? value : item) : [...items, value];
}

function updateDraft(policy: AccessPolicy, edit: AccessEdit): AccessPolicy {
  if (edit.kind === 'role') return { ...policy, roles: upsert(policy.roles, edit.value, (item) => item.id === edit.value.id) };
  if (edit.kind === 'group') return { ...policy, groups: upsert(policy.groups, edit.value, (item) => item.id === edit.value.id) };
  return { ...policy, users: upsert(policy.users, edit.value, (item) => item.user_id === edit.value.user_id) };
}

function removeFromDraft(policy: AccessPolicy, kind: 'role' | 'group', id: string): AccessPolicy {
  if (kind === 'role') return { ...policy, roles: policy.roles.filter((item) => item.id !== id),
    groups: policy.groups.map((group) => ({ ...group, role_ids: group.role_ids.filter((key) => key !== id) })),
    users: policy.users.map((user) => ({ ...user, role_ids: user.role_ids.filter((key) => key !== id) })) };
  return { ...policy, groups: policy.groups.filter((item) => item.id !== id),
    users: policy.users.map((user) => ({ ...user, group_ids: user.group_ids.filter((key) => key !== id) })) };
}

function effectiveFeatureCount(policy: AccessPolicy, assignment: UserAssignment): number {
  const allRoles = new Set(assignment.role_ids);
  policy.groups.filter((group) => assignment.group_ids.includes(group.id)).forEach((group) => group.role_ids.forEach((id) => allRoles.add(id)));
  const permissions = [...assignment.permissions, ...policy.roles.filter((role) => allRoles.has(role.id)).flatMap((role) => role.permissions)];
  return new Set(permissions.map((key) => key.split(':', 1)[0])).size;
}

export function FeatureAccessPanel({ users }: { users: AuthUser[] }) {
  const [policy, setPolicy] = useState<AccessPolicy>();
  const [edit, setEdit] = useState<AccessEdit>();
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(true);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  const load = async () => {
    const version = ++request.current;
    setBusy(true);
    setError('');
    try {
      const next = await getAccessPolicy();
      if (request.current === version) { setPolicy(next); setDirty(false); setConflict(false); }
    } catch (cause) {
      if (request.current === version) setError(cause instanceof Error ? cause.message : '功能授权读取失败');
    } finally { if (request.current === version) setBusy(false); }
  };
  useEffect(() => { void load(); return () => { request.current += 1; }; }, []);
  const save = async () => {
    if (!policy || !dirty || busy || conflict) return;
    const version = ++request.current;
    setBusy(true);
    setError('');
    try {
      const { revision, roles, groups, users: assignments } = policy;
      const next = await saveAccessPolicy(revision, { roles, groups, users: assignments });
      if (request.current === version) { setPolicy(next); setDirty(false); }
    } catch (cause) {
      if (request.current !== version) return;
      if (cause instanceof ApiError && cause.status === 409) {
        setConflict(true);
        setError('授权策略已被其他管理员修改，当前草稿仍保留。');
      } else setError(cause instanceof Error ? cause.message : '功能授权保存失败');
    } finally { if (request.current === version) setBusy(false); }
  };

  const reload = <Button title="重新加载授权策略" aria-label="重新加载授权策略" disabled={busy} icon={<RefreshCw size={16} />}
    onClick={dirty ? undefined : () => void load()} />;
  const actions = (item: CustomRole | UserGroup, kind: 'role' | 'group') => <Space>
    <Tooltip title="编辑"><Button type="text" aria-label={`编辑 ${item.name}`} icon={<Pencil size={16} />}
      onClick={() => setEdit('permissions' in item ? { kind: 'role', value: item } : { kind: 'group', value: item })} /></Tooltip>
    <Popconfirm title={`删除「${item.name}」及其授权引用？`} okText="确定" cancelText="取消" onConfirm={() => {
      setPolicy((current) => current && removeFromDraft(current, kind, item.id)); setDirty(true);
    }}><Button danger type="text" aria-label={`删除 ${item.name}`} icon={<Trash2 size={16} />} /></Popconfirm>
  </Space>;
  return <section className="feature-access-panel" aria-label="功能权限">
    <div className="feature-access-heading"><h2>功能权限</h2><Space wrap>
      {dirty ? <Tag color="orange">未保存</Tag> : null}
      {dirty ? <Popconfirm title="放弃当前草稿并重新加载？" okText="放弃草稿" cancelText="保留草稿"
        onConfirm={() => void load()}>{reload}</Popconfirm> : reload}
      <Button type="primary" loading={busy && Boolean(policy)} disabled={!dirty || conflict} onClick={() => void save()}>保存授权策略</Button>
    </Space></div>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    <Spin spinning={busy}>
      {policy ? <fieldset className="feature-access-tabs" disabled={busy}><Tabs items={[
        { key: 'roles', label: '自定义角色', children: <>
          <Button icon={<Plus size={16} />} onClick={() => setEdit({ kind: 'role', value: { id: newId(), name: '', permissions: [] } })}>新建角色</Button>
          <Table<CustomRole> rowKey="id" size="small" dataSource={policy.roles} locale={{ emptyText: '暂无自定义角色' }} columns={[
            { title: '角色', dataIndex: 'name' }, { title: '权限项', render: (_, item) => item.permissions.length },
            { title: '操作', width: 104, render: (_, item) => actions(item, 'role') },
          ]} />
        </> },
        { key: 'groups', label: '用户组', children: <>
          <Button icon={<Plus size={16} />} onClick={() => setEdit({ kind: 'group', value: { id: newId(), name: '', role_ids: [] } })}>新建用户组</Button>
          <Table<UserGroup> rowKey="id" size="small" dataSource={policy.groups} locale={{ emptyText: '暂无用户组' }} columns={[
            { title: '用户组', dataIndex: 'name' }, { title: '角色', render: (_, group) => policy.roles.filter((item) => group.role_ids.includes(item.id)).map((item) => item.name).join('、') || '—' },
            { title: '成员', render: (_, group) => policy.users.filter((item) => item.group_ids.includes(group.id)).length },
            { title: '操作', width: 104, render: (_, item) => actions(item, 'group') },
          ]} />
        </> },
        { key: 'users', label: '用户授权', children: <Table<AuthUser> rowKey="id" size="small" dataSource={users.filter((user) => user.role !== 'admin')}
          locale={{ emptyText: '暂无可配置功能权限的用户' }} columns={[
            { title: '用户名', dataIndex: 'username' }, { title: '授权状态', render: (_, user) => {
              const assignment = policy.users.find(({ user_id }) => user_id === user.id);
              const count = assignment ? effectiveFeatureCount(policy, assignment) : 0;
              return <Tag>{assignment ? (count ? `已开放 ${count} 项功能` : '未开放') : '沿用旧角色权限'}</Tag>;
            } },
            { title: '操作', width: 104, render: (_, user) => <Tooltip title="配置功能权限"><Button type="text" icon={<Pencil size={16} />}
              aria-label={`配置功能权限 ${user.username}`} onClick={() => setEdit({ kind: 'user', username: user.username,
                readOnly: user.role === 'auditor', value: policy.users.find(({ user_id }) => user_id === user.id)
                  ?? { user_id: user.id, permissions: [], role_ids: [], group_ids: [] } })} /></Tooltip> },
          ]} /> },
      ]} /></fieldset> : null}
    </Spin>
    {policy && edit ? <FeatureAccessEditor policy={policy} edit={edit} onCancel={() => setEdit(undefined)}
      onApply={(change) => { setPolicy(updateDraft(policy, change)); setDirty(true); setEdit(undefined); }} /> : null}
  </section>;
}
