import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Empty,
  Input,
  message,
  Popconfirm,
  Segmented,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { KeyRound, KeySquare, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';

import { createCredential, deleteCredential, getCredentials, updateCredential } from '../api/credentials';
import { CredentialFormDrawer } from '../components/CredentialFormDrawer';
import type { Credential, CredentialCreate, CredentialType, CredentialUpdate } from '../types/credential';

type FilterType = 'all' | CredentialType;

function formatDate(value: string | null): string {
  if (!value) return '从未使用';
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

export function CredentialsPage() {
  const [items, setItems] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterType>('all');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const load = async () => {
    setLoading(true);
    try { setItems(await getCredentials()); }
    catch (error) { messageApi.error(error instanceof Error ? error.message : '加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => items.filter((item) => {
    const matchesType = filter === 'all' || item.credential_type === filter;
    const keyword = query.trim().toLowerCase();
    const matchesQuery = !keyword || `${item.name} ${item.username}`.toLowerCase().includes(keyword);
    return matchesType && matchesQuery;
  }), [filter, items, query]);

  const save = async (payload: CredentialCreate | CredentialUpdate) => {
    setSubmitting(true);
    try {
      if (editing) await updateCredential(editing.id, payload as CredentialUpdate);
      else await createCredential(payload as CredentialCreate);
      messageApi.success(editing ? '凭据已更新' : '凭据已创建');
      setDrawerOpen(false);
      setEditing(null);
      await load();
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '保存失败'); }
    finally { setSubmitting(false); }
  };

  const remove = async (item: Credential) => {
    try {
      await deleteCredential(item.id);
      setItems((current) => current.filter((credential) => credential.id !== item.id));
      messageApi.success('凭据已删除');
    } catch (error) { messageApi.error(error instanceof Error ? error.message : '删除失败'); }
  };

  const columns: ColumnsType<Credential> = [
    { title: '名称', dataIndex: 'name', key: 'name', render: (name: string, item) => <div className="credential-name"><span className="credential-icon">{item.credential_type === 'password' ? <KeyRound size={17} /> : <KeySquare size={17} />}</span><strong>{name}</strong></div> },
    { title: '认证方式', dataIndex: 'credential_type', key: 'type', width: 130, render: (type: CredentialType) => <Tag color={type === 'password' ? 'green' : 'gold'}>{type === 'password' ? '密码' : 'SSH 私钥'}</Tag> },
    { title: '登录用户', dataIndex: 'username', key: 'username' },
    { title: '密钥', dataIndex: 'secret_mask', key: 'secret', width: 120, render: (mask: string) => <code className="secret-mask">{mask}</code> },
    { title: '关联资产', dataIndex: 'asset_count', key: 'assets', width: 100, render: (count: number) => `${count} 台` },
    { title: '最近使用', dataIndex: 'last_used_at', key: 'lastUsed', width: 180, render: formatDate },
    { title: '操作', key: 'actions', width: 104, align: 'right', render: (_, item) => <div className="row-actions"><Tooltip title="编辑"><Button type="text" icon={<Pencil size={17} />} aria-label={`编辑 ${item.name}`} onClick={() => { setEditing(item); setDrawerOpen(true); }} /></Tooltip><Popconfirm title="删除凭据" description="删除后无法恢复。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => remove(item)}><Tooltip title="删除"><Button type="text" danger icon={<Trash2 size={17} />} aria-label={`删除 ${item.name}`} /></Tooltip></Popconfirm></div> },
  ];

  const passwordCount = items.filter((item) => item.credential_type === 'password').length;
  const privateKeyCount = items.length - passwordCount;

  return (
    <section className="credentials-page">
      {contextHolder}
      <div className="page-heading"><div><p className="eyebrow">访问控制</p><h1>凭据管理</h1></div><Button type="primary" icon={<Plus size={17} />} onClick={() => { setEditing(null); setDrawerOpen(true); }}>新建凭据</Button></div>
      <div className="metric-band">
        <div><span>凭据总数</span><strong>{items.length}</strong></div>
        <div><span>密码认证</span><strong>{passwordCount}</strong></div>
        <div><span>私钥认证</span><strong>{privateKeyCount}</strong></div>
        <div><span>加密版本</span><strong className="metric-text">{items[0]?.key_version ?? 'v1'}</strong></div>
      </div>
      <div className="table-toolbar">
        <Input allowClear prefix={<Search size={16} />} placeholder="搜索名称或用户" value={query} onChange={(event) => setQuery(event.target.value)} />
        <Segmented value={filter} onChange={(value) => setFilter(value as FilterType)} options={[{ label: '全部', value: 'all' }, { label: '密码', value: 'password' }, { label: 'SSH 私钥', value: 'private_key' }]} />
        <Tooltip title="刷新"><Button icon={<RefreshCw size={17} />} aria-label="刷新" loading={loading} onClick={() => void load()} /></Tooltip>
      </div>
      <div className="table-frame">
        <Table<Credential> rowKey="id" columns={columns} dataSource={filtered} loading={loading} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无凭据"><Button type="primary" icon={<Plus size={16} />} onClick={() => setDrawerOpen(true)}>新建凭据</Button></Empty> }} scroll={{ x: 900 }} />
      </div>
      <CredentialFormDrawer open={drawerOpen} credential={editing} submitting={submitting} onClose={() => { setDrawerOpen(false); setEditing(null); }} onSubmit={save} />
    </section>
  );
}
