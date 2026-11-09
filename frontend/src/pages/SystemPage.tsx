import { useEffect, useState } from 'react';
import {
  Alert, Button, Checkbox, Descriptions, Form, Input, Modal, Select, Space, Spin,
  Switch, Table, Tag, message,
} from 'antd';
import {
  CheckCircle2, Eye, KeyRound, RefreshCw, ShieldCheck, UserPlus, XCircle,
} from 'lucide-react';

import { getSystemStatus, type SystemStatus } from '../api/system';
import {
  createUser, getUsers, resetUserPassword, updateUser,
  unlockUser,
  getUserAssetAccess, updateUserAssetAccess,
  type AssetAccessEntry, type AuthUser, type UserAccess, type UserCreate,
} from '../api/auth';
import { getAssets } from '../api/assets';
import type { Asset } from '../types/asset';
import { PASSWORD_POLICY_MESSAGE, validatePassword } from '../passwordPolicy';
import { AISettingsPanel } from '../components/AISettingsPanel';
import { AuthPolicyPanel } from '../components/AuthPolicyPanel';
import { RevokeUserSessionsModal, UserSessionsModal } from '../components/UserSessionsModal';
import { UserMfaModal } from '../components/UserMfaModal';
import { useUserMfa } from '../hooks/useUserMfa';

const emptyStatus: SystemStatus = {
  database_status: 'unavailable', database_engine: '-', schema_revision: null,
  monitoring_interval_seconds: 0, knowledge_sync_interval_seconds: 0,
  terminal: { max_connections: 0, idle_timeout_seconds: 0, max_duration_seconds: 0, host_key_verification: false },
  security: { scan_read_only_default: true, ai_direct_execution: false, credential_encryption: '-', authentication: '-' },
  diagnostic_scheduler: { enabled: false, status: 'disabled', stale_after_seconds: 10, started_at: null, last_loop_at: null, last_claim_at: null, recovered_jobs: 0, last_error: null, last_error_at: null, queued: 0, running: 0, retry_wait: 0, active_slots: 0 },
  health: { status: 'unknown', is_stale: true, stale_after_seconds: 300, last_observed_at: null },
  checked_at: '',
};

function duration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

const roleLabels = { admin: '管理员', operator: '操作员', auditor: '审计员' };

export function SystemPage({
  currentUser, onLogout,
}: { currentUser: AuthUser; onLogout?: () => void }) {
  const [status, setStatus] = useState<SystemStatus>(emptyStatus);
  const [loading, setLoading] = useState(true);
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [userLoading, setUserLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<AuthUser | null>(null);
  const [accessTarget, setAccessTarget] = useState<AuthUser | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [access, setAccess] = useState<UserAccess | null>(null);
  const [accessEntries, setAccessEntries] = useState<AssetAccessEntry[]>([]);
  const [accessScope, setAccessScope] = useState<'all' | 'assigned'>('assigned');
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessSaving, setAccessSaving] = useState(false);
  const [sessionTarget, setSessionTarget] = useState<AuthUser | null>(null);
  const [allSessionsTarget, setAllSessionsTarget] = useState<AuthUser | null>(null);
  const [createForm] = Form.useForm<UserCreate>();
  const [resetForm] = Form.useForm<{ password: string; must_change_password: boolean }>();
  const [accessForm] = Form.useForm<{ confirmation: string; reason: string }>();
  const [messages, holder] = message.useMessage();
  const load = async () => {
    setLoading(true);
    try { setStatus(await getSystemStatus()); }
    catch (error) { messages.error(error instanceof Error ? error.message : '系统状态加载失败'); }
    finally { setLoading(false); }
  };
  const loadUsers = async () => {
    if (currentUser.role !== 'admin') return;
    setUserLoading(true);
    try { setUsers(await getUsers()); }
    catch (error) { messages.error(error instanceof Error ? error.message : '用户列表加载失败'); }
    finally { setUserLoading(false); }
  };
  const mfa = useUserMfa({ currentUser, loadUsers, messages, onLogout });
  const loadAssets = async () => {
    if (currentUser.role !== 'admin') return;
    try { setAssets(await getAssets('active')); }
    catch (error) { messages.error(error instanceof Error ? error.message : '资产列表加载失败'); }
  };
  const openAccess = async (user: AuthUser) => {
    setAccessTarget(user);
    setAccessLoading(true);
    accessForm.resetFields();
    try {
      const result = await getUserAssetAccess(user.id);
      setAccess(result);
      setAccessScope(result.asset_scope);
      setAccessEntries(result.entries);
    } catch (error) {
      setAccessTarget(null);
      messages.error(error instanceof Error ? error.message : '资产权限加载失败');
    } finally { setAccessLoading(false); }
  };
  const closeAccess = () => {
    setAccessTarget(null);
    setAccess(null);
    setAccessEntries([]);
    accessForm.resetFields();
  };
  const setAssetView = (assetId: string, enabled: boolean) => {
    setAccessEntries((items) => {
      const current = items.find((item) => item.asset_id === assetId);
      if (!enabled) return items.filter((item) => item.asset_id !== assetId);
      if (current) return items;
      return [...items, { asset_id: assetId, can_ssh: false }];
    });
  };
  const setAssetSsh = (assetId: string, enabled: boolean) => {
    setAccessEntries((items) => items.map((item) => (
      item.asset_id === assetId ? { ...item, can_ssh: enabled } : item
    )));
  };
  const submitAccess = async (values: { confirmation: string; reason: string }) => {
    if (!accessTarget || !access) return;
    setAccessSaving(true);
    try {
      await updateUserAssetAccess(accessTarget.id, {
        asset_scope: accessScope, entries: accessScope === 'all' ? [] : accessEntries,
        expected_revision: access.revision, reason: values.reason,
        confirmation: values.confirmation,
      });
      messages.success('资产权限已保存，旧 SSH 票据已失效');
      closeAccess();
      await loadUsers();
    } catch (error) { messages.error(error instanceof Error ? error.message : '资产权限保存失败'); }
    finally { setAccessSaving(false); }
  };
  const submitUser = async (values: UserCreate) => {
    try {
      await createUser(values);
      setCreateOpen(false);
      createForm.resetFields();
      messages.success('用户已创建');
      await loadUsers();
    } catch (error) { messages.error(error instanceof Error ? error.message : '用户创建失败'); }
  };
  const toggleUser = async (user: AuthUser, enabled: boolean) => {
    try {
      await updateUser(user.id, { enabled });
      messages.success(enabled ? '用户已启用' : '用户已停用并撤销会话');
      await loadUsers();
    } catch (error) { messages.error(error instanceof Error ? error.message : '用户状态更新失败'); }
  };
  const submitReset = async ({ password, must_change_password }: {
    password: string; must_change_password: boolean;
  }) => {
    if (!resetTarget) return;
    try {
      await resetUserPassword(resetTarget.id, password, must_change_password);
      setResetTarget(null);
      resetForm.resetFields();
      messages.success('密码已重置，原会话已撤销');
      await loadUsers();
    } catch (error) { messages.error(error instanceof Error ? error.message : '密码重置失败'); }
  };
  const unlock = async (user: AuthUser) => {
    try {
      await unlockUser(user.id);
      messages.success('登录锁定已解除');
      await loadUsers();
    } catch (error) {
      messages.error(error instanceof Error ? error.message : '解除登录锁定失败');
    }
  };
  const closeReset = () => {
    setResetTarget(null);
    resetForm.resetFields();
  };
  const openReset = (user: AuthUser) => {
    resetForm.setFieldsValue({ password: '', must_change_password: false });
    setResetTarget(user);
  };
  useEffect(() => { void load(); void loadUsers(); void loadAssets(); }, []);
  useEffect(() => { setSessionTarget(null); setAllSessionsTarget(null); }, [currentUser.id, currentUser.role, currentUser.access_revision]);
  const databaseOk = status.database_status === 'ok';
  const health = status.health;
  const healthStatus = health?.status ?? (databaseOk ? 'healthy' : 'unavailable');
  const healthStale = health?.is_stale ?? false;
  const healthLabel = healthStatus === 'healthy' && !healthStale ? '核心服务运行正常'
    : healthStatus === 'degraded' ? '平台健康状态降级'
      : healthStatus === 'unavailable' ? '平台服务不可用' : '平台健康状态未知';
  const healthType = healthStatus === 'healthy' && !healthStale ? 'success' : healthStatus === 'unavailable' ? 'error' : 'warning';
  return <section className="system-page">{holder}
    <div className="page-heading"><div><p className="eyebrow">平台运行信息</p><h1>系统管理</h1></div><Button aria-label="刷新系统状态" icon={<RefreshCw size={17} />} loading={loading} onClick={() => void load()} /></div>
    <Spin spinning={loading}>
      <Alert type={healthType} showIcon icon={healthType === 'success' ? <CheckCircle2 size={18} /> : <XCircle size={18} />} message={healthLabel} description={status.checked_at ? `页面检查：${new Date(status.checked_at).toLocaleString('zh-CN')}；最近调度观测：${health?.last_observed_at ? new Date(health.last_observed_at).toLocaleString('zh-CN') : '未收到'}${healthStale ? '；检查陈旧或未启用，不能据此判定正常' : ''}` : '尚未完成检查'} />
      <div className="metric-band system-metrics"><div><span>数据库</span><strong>{databaseOk ? '正常' : '异常'}</strong><small>{status.database_engine}</small></div><div><span>数据库结构</span><strong className="metric-text">{status.schema_revision ?? '未迁移'}</strong><small>当前 Alembic 版本</small></div><div><span>监控采集周期</span><strong>{duration(status.monitoring_interval_seconds)}</strong><small>SSH 无 Agent</small></div><div><span>知识库同步周期</span><strong>{duration(status.knowledge_sync_interval_seconds)}</strong><small>服务端调度</small></div></div>
      <div className="system-grid">
        <section className="ops-panel"><div className="panel-heading"><div><strong>终端连接策略</strong><span>Web SSH 网关运行边界</span></div></div><Descriptions column={1} size="small" items={[{ key: 'connections', label: '最大并发连接', children: `${status.terminal.max_connections} 个` }, { key: 'idle', label: '空闲超时', children: duration(status.terminal.idle_timeout_seconds) }, { key: 'duration', label: '最长会话', children: duration(status.terminal.max_duration_seconds) }, { key: 'host-key', label: '主机密钥策略', children: <Tag color="green">首次连接自动接受，仅内存使用</Tag> }]} /></section>
        <section className="ops-panel"><div className="panel-heading"><div><strong>安全边界</strong><span>只读与凭据保护策略</span></div><ShieldCheck size={20} /></div><Descriptions column={1} size="small" items={[{ key: 'scan', label: '扫描默认模式', children: status.security.scan_read_only_default ? <Tag color="green">只读</Tag> : <Tag color="red">需检查</Tag> }, { key: 'ai', label: 'AI 直接执行', children: status.security.ai_direct_execution ? <Tag color="red">允许</Tag> : <Tag color="green">禁止</Tag> }, { key: 'encryption', label: '凭据加密', children: status.security.credential_encryption }, { key: 'auth', label: '认证方式', children: status.security.authentication }]} /></section>
      </div>
      {currentUser.role === 'admin' ? <AuthPolicyPanel /> : null}
      {currentUser.role === 'admin' ? <AISettingsPanel /> : null}
      {currentUser.role === 'admin' ? <section className="user-management">
        <div className="panel-heading">
          <div><strong>用户与权限</strong><span>用户密码不可查看，停用和重置密码会撤销已有会话</span></div>
          <Button type="primary" icon={<UserPlus size={17} />} onClick={() => setCreateOpen(true)}>新建用户</Button>
        </div>
        <div className="table-frame"><Table<AuthUser>
          rowKey="id" loading={userLoading} dataSource={users} pagination={false}
          columns={[
            { title: '用户', render: (_, item) => <div><strong>{item.display_name || item.username}</strong><br /><small>{item.username}</small></div> },
            { title: '角色', dataIndex: 'role', render: (role: AuthUser['role'], item) => <Space><Tag>{roleLabels[role]}</Tag>{role !== 'admin' ? <Tag color={item.asset_scope === 'all' ? 'blue' : 'default'}>{item.asset_scope === 'all' ? '全部资产' : '指定资产'}</Tag> : null}</Space> },
            { title: '状态', dataIndex: 'enabled', render: (enabled: boolean, item) => <Space><Switch aria-label={`${item.username}状态`} checked={enabled} disabled={item.id === currentUser.id} onChange={(value) => void toggleUser(item, value)} />{item.locked_until && new Date(item.locked_until).getTime() > Date.now() ? <Tag color="red">登录锁定</Tag> : null}<Tag color={item.mfa_enabled ? 'green' : 'default'}>{item.mfa_enabled ? 'MFA 已启用' : 'MFA 未启用'}</Tag></Space> },
            { title: '最近登录', dataIndex: 'last_login_at', render: (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '从未登录' },
            { title: '操作', align: 'right', render: (_, item) => <Space>
              <Button aria-label={`重置 ${item.username} 密码`} icon={<KeyRound size={16} />} onClick={() => openReset(item)}>重置密码</Button>
              {item.locked_until && new Date(item.locked_until).getTime() > Date.now() ? <Button aria-label={`解锁 ${item.username}`} onClick={() => void unlock(item)}>解除锁定</Button> : null}
              {item.mfa_enabled ? <Button aria-label={`重置 ${item.username} MFA`} onClick={() => mfa.reset(item)}>重置 MFA</Button> : <Button aria-label={`设置 ${item.username} MFA`} onClick={() => void mfa.open(item)}>设置 MFA</Button>}
              {item.role !== 'admin' ? <Button onClick={() => void openAccess(item)}>资产授权</Button> : null}
              <Button aria-label={`查看 ${item.username} 活动会话`} icon={<Eye size={16} />} onClick={() => setSessionTarget(item)}>活动会话</Button>
              <Button onClick={() => setAllSessionsTarget(item)}>撤销全部会话</Button>
            </Space> },
          ]}
        /></div>
      </section> : null}
    </Spin>
    <Modal title="新建平台用户" open={createOpen} footer={null} onCancel={() => setCreateOpen(false)} destroyOnHidden>
      <Form form={createForm} layout="vertical" onFinish={(values) => void submitUser(values)} initialValues={{ role: 'operator', asset_scope: 'assigned' }}>
        <Form.Item name="username" label="用户名" rules={[{ required: true, min: 3, pattern: /^[A-Za-z0-9._-]+$/ }]}><Input autoComplete="off" /></Form.Item>
        <Form.Item name="display_name" label="显示名称"><Input maxLength={120} /></Form.Item>
        <Form.Item name="role" label="角色" rules={[{ required: true }]}><Select options={Object.entries(roleLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
        <Form.Item name="asset_scope" label="资产范围" rules={[{ required: true }]}><Select options={[{ value: 'assigned', label: '指定资产（默认）' }, { value: 'all', label: '全部资产' }]} /></Form.Item>
        <Form.Item name="password" label="初始密码" extra={PASSWORD_POLICY_MESSAGE} rules={[
          { required: true, message: PASSWORD_POLICY_MESSAGE },
          { validator: validatePassword },
        ]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
        <Button block type="primary" htmlType="submit">创建用户</Button>
      </Form>
    </Modal>
    <Modal title={`重置密码${resetTarget ? `：${resetTarget.username}` : ''}`} open={resetTarget !== null} footer={null} onCancel={closeReset} destroyOnHidden>
      <Form form={resetForm} layout="vertical" initialValues={{ must_change_password: false }} onFinish={(values) => void submitReset(values)}>
        <Alert type="warning" showIcon message="重置后该用户的所有已登录会话将立即失效。" />
        <Form.Item name="password" label="新密码" extra={PASSWORD_POLICY_MESSAGE} rules={[
          { required: true, message: PASSWORD_POLICY_MESSAGE },
          { validator: validatePassword },
        ]} style={{ marginTop: 16 }}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
        <Form.Item name="must_change_password" valuePropName="checked">
          <Checkbox>要求用户下次登录修改密码</Checkbox>
        </Form.Item>
        <Button block type="primary" htmlType="submit">确认重置</Button>
      </Form>
    </Modal>
    <UserMfaModal {...mfa} />
    <Modal
      title={`资产授权${accessTarget ? `：${accessTarget.username}` : ''}`}
      open={accessTarget !== null}
      footer={null}
      onCancel={closeAccess}
      destroyOnHidden
      width={720}
    >
      <Spin spinning={accessLoading}>
        <Alert type="info" showIcon message="查看权限控制资产目录、漏洞和监控数据的可见范围；SSH 权限仅允许操作员使用，AI 仍保持只读。" />
        <Form form={accessForm} layout="vertical" onFinish={(values) => void submitAccess(values)} style={{ marginTop: 16 }}>
          <Form.Item label="资产范围">
            <Select value={accessScope} onChange={(value: 'all' | 'assigned') => { setAccessScope(value); if (value === 'all') setAccessEntries([]); }} options={[{ value: 'assigned', label: '指定资产' }, { value: 'all', label: '全部资产' }]} />
          </Form.Item>
          {accessScope === 'assigned' ? <div className="table-frame">
            <Table<Asset> rowKey="id" dataSource={assets} pagination={false} size="small" locale={{ emptyText: '暂无可授权资产' }} columns={[
              { title: '服务器', render: (_, item) => <div><strong>{item.name}</strong><br /><small>{item.host}</small></div> },
              { title: '查看', render: (_, item) => <Checkbox aria-label={`${item.name}查看权限`} checked={accessEntries.some((entry) => entry.asset_id === item.id)} onChange={(event) => setAssetView(item.id, event.target.checked)} /> },
              { title: 'SSH', render: (_, item) => { const entry = accessEntries.find((candidate) => candidate.asset_id === item.id); return <Checkbox aria-label={`${item.name}SSH权限`} checked={entry?.can_ssh === true} disabled={!entry || accessTarget?.role === 'auditor'} onChange={(event) => setAssetSsh(item.id, event.target.checked)} />; } },
            ]} />
          </div> : <Alert type="warning" showIcon message="全部资产范围会包含后续新增资产，请仅对确有需要的账号启用。" />}
          <Form.Item name="confirmation" label={accessTarget ? `输入用户名确认：${accessTarget.username}` : '输入用户名确认'} rules={[{ required: true, validator: (_, value: string) => value === accessTarget?.username ? Promise.resolve() : Promise.reject(new Error('请输入完全一致的用户名')) }]} style={{ marginTop: 16 }}><Input autoComplete="off" /></Form.Item>
          <Form.Item name="reason" label="变更原因" rules={[{ required: true, min: 5, message: '请填写至少 5 个字符的原因' }]}><Input.TextArea maxLength={500} rows={2} /></Form.Item>
          <Button block type="primary" htmlType="submit" loading={accessSaving} disabled={accessLoading || !access}>保存资产授权</Button>
        </Form>
      </Spin>
    </Modal>
    {currentUser.role === 'admin' && currentUser.enabled && !currentUser.must_change_password
      ? <UserSessionsModal key={`sessions:${currentUser.id}:${currentUser.access_revision ?? 0}`}
        target={sessionTarget} onClose={() => setSessionTarget(null)} onLogout={onLogout} /> : null}
    {currentUser.role === 'admin' && currentUser.enabled && !currentUser.must_change_password
      ? <RevokeUserSessionsModal key={`revoke:${currentUser.id}:${currentUser.access_revision ?? 0}`}
        target={allSessionsTarget} currentUserId={currentUser.id} onLogout={onLogout}
        onClose={() => { setAllSessionsTarget(null); setSessionTarget(null); }} /> : null}
  </section>;
}
