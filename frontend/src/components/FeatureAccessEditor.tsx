import { Checkbox, Form, Input, Modal, Select } from 'antd';
import { useState } from 'react';
import type { AccessPolicy, CatalogItem, CustomRole, UserAssignment, UserGroup } from '../api/accessPolicy';

export type AccessEdit = { kind: 'role'; value: CustomRole } | { kind: 'group'; value: UserGroup }
  | { kind: 'user'; value: UserAssignment; username: string; readOnly: boolean };

function PermissionFields({ catalog, value, onChange, readOnly }: {
  catalog: CatalogItem[]; value: string[]; onChange: (value: string[]) => void; readOnly: boolean;
}) {
  return <div className="feature-permission-list" role="group" aria-label="功能权限">
    {catalog.map(({ page, label, write }) => {
      const view = `${page}:read`;
      const manage = `${page}:write`;
      const managed = value.includes(manage);
      const select = (key: string, checked: boolean) => onChange(checked ? [...value, key] : value.filter((item) => item !== key));
      return <div className="feature-permission-row" key={page}>
        <span>{label}</span>
        <Checkbox aria-label={`${label} 查看`} checked={managed || value.includes(view)} disabled={managed && !readOnly}
          onChange={(event) => select(view, event.target.checked)}>查看</Checkbox>
        {write ? <Checkbox aria-label={`${label} 操作`} disabled={readOnly} checked={managed && !readOnly}
          onChange={(event) => select(manage, event.target.checked)}>操作</Checkbox> : null}
      </div>;
    })}
  </div>;
}

export function FeatureAccessEditor({ edit, policy, onApply, onCancel }: {
  edit: AccessEdit; policy: AccessPolicy; onApply: (edit: AccessEdit) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<AccessEdit>(edit);
  const roleOptions = policy.roles.map(({ id, name }) => ({ value: id, label: name }));
  const groupOptions = policy.groups.map(({ id, name }) => ({ value: id, label: name }));
  const labels = { role: '角色名称', group: '用户组名称', user: '用户授权' };
  const label = labels[draft.kind];
  const name = draft.kind === 'user' ? '' : draft.value.name.trim();
  const names = draft.kind === 'role' ? policy.roles : draft.kind === 'group' ? policy.groups : [];
  const id = draft.kind === 'user' ? draft.value.user_id : draft.value.id;
  const duplicate = draft.kind !== 'user' && names.some((item) => item.id !== id
    && item.name.toLowerCase() === name.toLowerCase());
  const apply = () => {
    if (draft.kind === 'user') {
      const permissions = draft.readOnly ? [...new Set(draft.value.permissions.map((key) => key.replace(/:write$/, ':read')))]
        : draft.value.permissions;
      onApply({ ...draft, value: { ...draft.value, permissions } });
    } else if (name && !duplicate) {
      if (draft.kind === 'role') onApply({ ...draft, value: { ...draft.value, name } });
      else onApply({ ...draft, value: { ...draft.value, name } });
    }
  };
  return <Modal open title={draft.kind === 'user' ? `用户授权 · ${draft.username}` : draft.kind === 'role' ? '自定义角色' : '用户组'}
    okText="应用到草稿" cancelText="取消" onOk={apply} onCancel={onCancel} width={660}
    okButtonProps={{ disabled: draft.kind !== 'user' && (!name || duplicate) }}>
    <Form layout="vertical" component="div">
      {draft.kind !== 'user' ? <Form.Item label={label} htmlFor="feature-access-name" validateStatus={duplicate ? 'error' : undefined}
        help={duplicate ? '名称已存在' : undefined}>
        <Input id="feature-access-name" maxLength={80} value={draft.value.name} onChange={(event) => {
          const next = event.target.value;
          setDraft((current) => current.kind === 'role' ? { ...current, value: { ...current.value, name: next } }
            : current.kind === 'group' ? { ...current, value: { ...current.value, name: next } } : current);
        }} />
      </Form.Item> : null}
      {draft.kind === 'user' ? <Form.Item label="用户组">
        <Select aria-label="用户组" mode="multiple" options={groupOptions} value={draft.value.group_ids} maxCount={32}
          onChange={(group_ids) => setDraft({ ...draft, value: { ...draft.value, group_ids } })} />
      </Form.Item> : null}
      {draft.kind !== 'role' ? <Form.Item label="角色">
        <Select aria-label="角色" mode="multiple" options={roleOptions} value={draft.value.role_ids} maxCount={32}
          onChange={(role_ids) => setDraft((current) => current.kind === 'role' ? current
            : current.kind === 'group' ? { ...current, value: { ...current.value, role_ids } }
              : { ...current, value: { ...current.value, role_ids } })} />
      </Form.Item> : null}
      {draft.kind !== 'group' ? <Form.Item label={draft.kind === 'user' ? '个人追加权限' : '功能权限'}>
        <PermissionFields catalog={policy.catalog} value={draft.value.permissions} readOnly={draft.kind === 'user' && draft.readOnly}
          onChange={(permissions) => setDraft((current) => current.kind === 'group' ? current
            : current.kind === 'role' ? { ...current, value: { ...current.value, permissions } }
              : { ...current, value: { ...current.value, permissions } })} />
      </Form.Item> : null}
    </Form>
  </Modal>;
}
