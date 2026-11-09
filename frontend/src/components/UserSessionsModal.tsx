import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Grid, Modal, Space, Spin, Table, Tag, message } from 'antd';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { revokeUserSessions, type AuthSession, type AuthUser } from '../api/auth';
import { logoutRevokedSession, useUserSessions } from '../hooks/useUserSessions';

const roleLabels = { admin: '管理员', operator: '操作员', auditor: '审计员' };
const sessionTime = (value: string) => new Date(value).toLocaleString('zh-CN');
const sessionClient = (value: string | null) => value || '未提供客户端信息';

function SessionStatus({ item }: { item: AuthSession }) {
  if (item.is_current) return <Tag color="blue">当前会话</Tag>;
  const remaining = new Date(item.expires_at).getTime() - Date.now();
  return remaining <= 2 * 60 * 60 * 1000
    ? <Tag color="orange">即将过期</Tag> : <Tag color="green">活跃</Tag>;
}

interface Props {
  target: AuthUser | null;
  onClose: () => void;
  onLogout?: () => void;
}

export function UserSessionsModal({ target, onClose, onLogout }: Props) {
  return target ? <SessionContext key={target.id} target={target}
    onClose={onClose} onLogout={onLogout} /> : null;
}

function SessionList({ state }: {
  state: Pick<ReturnType<typeof useUserSessions>, 'sessions' | 'saving' | 'setRevokeTarget'>;
}) {
  const screens = Grid.useBreakpoint();
  return screens.md ? <div className="session-table">
    <Table<AuthSession> rowKey="id" dataSource={state.sessions} pagination={false}
      scroll={{ x: 1200 }} locale={{ emptyText: '暂无活动会话' }} columns={[
        { title: '用户', render: (_, item) => <div><strong>{item.display_name || item.username}</strong><br /><small>{item.username}</small></div> },
        { title: '角色', dataIndex: 'role', render: (role: AuthSession['role']) => roleLabels[role] },
        { title: '登录时间', dataIndex: 'created_at', render: sessionTime },
        { title: '最近活动', dataIndex: 'last_used_at', render: sessionTime },
        { title: '过期时间', dataIndex: 'expires_at', render: sessionTime },
        { title: '来源 IP', dataIndex: 'client_ip', render: (value: string | null) => value || '未知' },
        { title: '客户端', dataIndex: 'user_agent', render: (value: string | null) => <span className="session-client" title={value || undefined}>{sessionClient(value)}</span> },
        { title: '状态', render: (_, item) => <SessionStatus item={item} /> },
        { title: '操作', align: 'right', render: (_, item) => <Button
          type="link" danger disabled={state.saving}
          onClick={() => state.setRevokeTarget(item)}>撤销会话</Button> },
      ]} />
  </div> : <div className="session-list-mobile">
    {state.sessions.map((item) => <article className="session-mobile-item" key={item.id}>
      <div className="session-mobile-top"><strong>{item.display_name || item.username}</strong><SessionStatus item={item} /></div>
      <div className="session-mobile-meta">{roleLabels[item.role]} · {item.username}</div>
      <div className="session-mobile-meta">登录时间 {sessionTime(item.created_at)}</div>
      <div className="session-mobile-meta">最近活动 {sessionTime(item.last_used_at)} · {item.client_ip || '未知 IP'}</div>
      <div className="session-mobile-meta">过期时间 {sessionTime(item.expires_at)}</div>
      <div className="session-mobile-client" title={item.user_agent || undefined}>{sessionClient(item.user_agent)}</div>
      <Button danger block disabled={state.saving} onClick={() => state.setRevokeTarget(item)}>撤销会话</Button>
    </article>)}
    {!state.sessions.length ? <div className="session-empty">暂无活动会话</div> : null}
  </div>;
}

function SessionContext({ target, onClose, onLogout }: Props & { target: AuthUser }) {
  const state = useUserSessions(target.id, onLogout);
  return <>{state.holder}
    <Modal title={`活动会话：${target.display_name || target.username}`}
      open footer={null} onCancel={() => { if (!state.saving) onClose(); }}
      closable={!state.saving} maskClosable={!state.saving} keyboard={!state.saving}
      width={980} destroyOnHidden>
      <div className="session-panel-heading">
        <span>仅显示有效的浏览器登录会话；撤销后下一次请求将被拒绝。</span>
        <Button aria-label="刷新活动会话" icon={<RefreshCw size={16} />}
          loading={state.loading} disabled={state.saving} onClick={() => void state.load()} />
      </div>
      {state.error ? <Alert type="error" showIcon message={state.error} /> : null}
      <Spin spinning={state.loading}>
        {!state.error && !state.loading ? <SessionList state={state} /> : null}
      </Spin>
      {!state.error && !state.loading ? <nav aria-label="活动会话分页">
        <Space wrap>
          <Button aria-label="上一页" icon={<ChevronLeft size={16} />}
            disabled={state.saving || state.page <= 1}
            onClick={() => state.changePage(state.page - 1)}>上一页</Button>
          <span>第 {state.page} / {Math.max(1, Math.ceil(state.total / state.pageSize))} 页 · 共 {state.total} 个会话</span>
          <Button aria-label="下一页" icon={<ChevronRight size={16} />}
            disabled={state.saving || state.page * state.pageSize >= state.total}
            onClick={() => state.changePage(state.page + 1)}>下一页</Button>
        </Space>
      </nav> : null}
    </Modal>
    <Modal title="撤销此活动会话？" aria-label="撤销此活动会话？" open={state.revokeTarget !== null}
      onCancel={() => { if (!state.saving) state.setRevokeTarget(null); }}
      onOk={() => void state.revoke()} okText="确认撤销" cancelText="取消"
      okButtonProps={{ danger: true, loading: state.saving }}
      cancelButtonProps={{ disabled: state.saving, 'aria-label': '取消' }} closable={!state.saving}
      maskClosable={!state.saving} keyboard={!state.saving} destroyOnHidden>
      {state.revokeTarget ? <Alert type="warning" showIcon
        message={`${state.revokeTarget.display_name || state.revokeTarget.username} · ${state.revokeTarget.client_ip || '未知 IP'}`}
        description={`最近活动：${sessionTime(state.revokeTarget.last_used_at)}；客户端：${sessionClient(state.revokeTarget.user_agent)}。${state.revokeTarget.is_current ? '这是当前登录会话，确认后将退出登录。' : '撤销后该浏览器的登录凭证将失效。'}`} /> : null}
    </Modal>
  </>;
}

export function RevokeUserSessionsModal(props: Props & { currentUserId: string }) {
  return props.target ? <RevokeAllContext key={props.target.id}
    {...props} target={props.target} /> : null;
}

function RevokeAllContext({ target, currentUserId, onClose, onLogout }:
  Props & { target: AuthUser; currentUserId: string }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const inFlight = useRef(false);
  const [messages, holder] = message.useMessage();
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const submit = async () => {
    if (inFlight.current || error) return;
    inFlight.current = true; setSaving(true);
    try {
      await revokeUserSessions(target.id);
      if (!active.current) return;
      messages.success('已撤销该用户全部会话');
      if (target.id === currentUserId) await logoutRevokedSession(onLogout);
      onClose();
    } catch (failure) {
      if (active.current) setError(`${failure instanceof Error ? failure.message : '撤销请求异常'}；请关闭弹窗并刷新确认会话状态`);
    } finally {
      inFlight.current = false;
      if (active.current) setSaving(false);
    }
  };
  return <>{holder}<Modal open title={`撤销全部会话：${target.username}`}
    onCancel={() => { if (!saving) onClose(); }} onOk={() => void submit()}
    okText="确认撤销全部会话" cancelText="取消" closable={!saving}
    maskClosable={!saving} keyboard={!saving}
    okButtonProps={{ danger: true, loading: saving, disabled: !!error }}
    cancelButtonProps={{ disabled: saving }}>
    <Alert type="warning" showIcon message={`将撤销 ${target.display_name || target.username} 的全部登录会话`}
      description={`包括已建立的 SSH 连接。${target.id === currentUserId ? '当前账号也会退出登录。' : '该用户需要重新登录。'}`} />
    {error ? <Alert type="error" showIcon message={error} /> : null}
  </Modal></>;
}
