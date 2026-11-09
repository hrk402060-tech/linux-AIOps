import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { getActiveSessionsPage, revokeUserSession, revokeUserSessions, type AuthSession, type AuthSessionPage, type AuthUser } from '../api/auth';
import { useUserSessions } from '../hooks/useUserSessions';
import { RevokeUserSessionsModal, UserSessionsModal } from './UserSessionsModal';

vi.mock('../api/auth', () => ({
  getActiveSessionsPage: vi.fn(), revokeUserSession: vi.fn(), revokeUserSessions: vi.fn(), logout: vi.fn(),
}));

const user: AuthUser = {
  id: 'user-1', username: 'ops-user', display_name: '运维用户', role: 'operator',
  enabled: true, must_change_password: false, created_at: '2026-10-29T01:00:00Z',
  last_login_at: null,
};
const session: AuthSession = {
  id: 'session-1', user_id: user.id, username: user.username,
  display_name: user.display_name, role: user.role, client_ip: '10.0.0.8',
  user_agent: 'Session Test Browser', created_at: '2026-10-29T01:00:00Z',
  last_used_at: '2026-10-29T02:00:00Z', expires_at: '2099-10-30T01:00:00Z',
  status: 'active', is_current: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function page(items: AuthSession[] = [session], total = items.length, offset = 0): AuthSessionPage {
  return { items, total, offset, limit: 20 };
}

function view(target: AuthUser | null = user, onClose = vi.fn()) {
  return <ConfigProvider theme={{ token: { motion: false } }}>
    <UserSessionsModal target={target} onClose={onClose} />
  </ConfigProvider>;
}

async function openConfirmation() {
  await screen.findByText(session.user_agent!);
  fireEvent.click(screen.getByRole('button', { name: '撤销会话' }));
  const title = await screen.findByText('撤销此活动会话？');
  return within(title.closest('[role="dialog"]') as HTMLElement);
}

beforeEach(() => {
  vi.mocked(getActiveSessionsPage).mockResolvedValue(page());
  vi.mocked(revokeUserSession).mockResolvedValue(undefined);
});
afterEach(() => vi.resetAllMocks());

it('shows expiry and requires explicit confirmation before revocation', async () => {
  render(view());
  const confirmation = await openConfirmation();
  expect(screen.getByText(/过期时间/)).toBeInTheDocument();
  expect(revokeUserSession).not.toHaveBeenCalled();
  fireEvent.click(confirmation.getByRole('button', { name: '取消' }));
  await waitFor(() => expect(screen.queryByText('撤销此活动会话？')).toBeNull());
  expect(revokeUserSession).not.toHaveBeenCalled();
});

it('invalidates the list and pending confirmation when refresh fails', async () => {
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page())
    .mockRejectedValueOnce(new Error('网络暂不可用')).mockResolvedValueOnce(page([]));
  render(view());
  await openConfirmation();
  fireEvent.click(screen.getByRole('button', { name: '刷新活动会话' }));
  expect(await screen.findByText(/网络暂不可用/)).toBeInTheDocument();
  expect(screen.queryByText('撤销此活动会话？')).toBeNull();
  expect(screen.queryByRole('button', { name: '撤销会话' })).toBeNull();
  expect(screen.queryByText('暂无活动会话')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '刷新活动会话' }));
  expect(await screen.findByText('暂无活动会话')).toBeInTheDocument();
});

it('discards a late response after switching users', async () => {
  const late = deferred<AuthSessionPage>();
  vi.mocked(getActiveSessionsPage).mockReturnValueOnce(late.promise).mockResolvedValueOnce(page([]));
  const { rerender } = render(view());
  await waitFor(() => expect(getActiveSessionsPage).toHaveBeenCalledWith(user.id, 0, 20));
  rerender(view({ ...user, id: 'user-2', username: 'other-user' }));
  expect(await screen.findByText('暂无活动会话')).toBeInTheDocument();
  await act(async () => { late.resolve(page()); });
  await waitFor(() => expect(getActiveSessionsPage).toHaveBeenCalledWith('user-2', 0, 20));
  expect(screen.queryByText(session.user_agent!)).toBeNull();
  expect(revokeUserSession).not.toHaveBeenCalled();
});

it('rejects session metadata belonging to a different user', async () => {
  vi.mocked(getActiveSessionsPage).mockResolvedValue(page([{ ...session, user_id: 'other-user' }]));
  render(view());
  expect(await screen.findByText(/会话归属校验失败/)).toBeInTheDocument();
  expect(screen.queryByText(session.user_agent!)).toBeNull();
  expect(screen.queryByRole('button', { name: '撤销会话' })).toBeNull();
});

it('requires refresh after an uncertain revocation and never automatically retries', async () => {
  vi.mocked(revokeUserSession).mockRejectedValueOnce(new Error('请求超时'));
  render(view());
  const confirmation = await openConfirmation();
  fireEvent.click(confirmation.getByRole('button', { name: '确认撤销' }));
  expect(await screen.findByText(/请刷新确认会话状态/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '撤销会话' })).toBeNull();
  expect(revokeUserSession).toHaveBeenCalledTimes(1);
  expect(getActiveSessionsPage).toHaveBeenCalledTimes(1);
});

it('ignores a completed revocation after the modal has unmounted', async () => {
  const late = deferred<void>();
  vi.mocked(revokeUserSession).mockReturnValueOnce(late.promise);
  const { rerender } = render(view());
  const confirmation = await openConfirmation();
  fireEvent.click(confirmation.getByRole('button', { name: '确认撤销' }));
  await waitFor(() => expect(revokeUserSession).toHaveBeenCalledOnce());
  rerender(view(null));
  await act(async () => { late.resolve(undefined); });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.queryByText('已撤销该活动会话')).toBeNull();
  expect(getActiveSessionsPage).toHaveBeenCalledTimes(1);
});

it('requests server pages and preserves the selected page on refresh', async () => {
  const second = { ...session, id: 'session-21', user_agent: 'Page Two Browser' };
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 21))
    .mockResolvedValue(page([second], 21, 20));
  render(view());
  await screen.findByText(session.user_agent!);
  expect(getActiveSessionsPage).toHaveBeenCalledWith(user.id, 0, 20);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(await screen.findByText(second.user_agent!)).toBeInTheDocument();
  expect(getActiveSessionsPage).toHaveBeenLastCalledWith(user.id, 20, 20);
  expect(screen.queryByText(session.user_agent!)).toBeNull();
  expect(screen.getByText('第 2 / 2 页 · 共 21 个会话')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新活动会话' }));
  await waitFor(() => expect(getActiveSessionsPage).toHaveBeenCalledTimes(3));
  expect(getActiveSessionsPage).toHaveBeenLastCalledWith(user.id, 20, 20);
});

it('does not let an older page response overwrite the latest page', async () => {
  const late = deferred<AuthSessionPage>();
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 21))
    .mockReturnValueOnce(late.promise).mockResolvedValueOnce(page([session], 21));
  const { result } = renderHook(() => useUserSessions(user.id));
  await waitFor(() => expect(result.current.loading).toBe(false));
  let previous: Promise<void> | undefined;
  act(() => { previous = result.current.load(2); });
  await act(async () => { await result.current.load(1); });
  await act(async () => {
    late.resolve(page([{ ...session, id: 'old-page', user_agent: 'Old Page' }], 21, 20));
    await previous;
  });
  expect(result.current.page).toBe(1);
  expect(result.current.sessions).toEqual([session]);
});

it('clears page contents on a failed page request until manual refresh', async () => {
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 21))
    .mockRejectedValueOnce(new Error('分页暂不可用'))
    .mockResolvedValueOnce(page([{ ...session, user_agent: 'Recovered Browser' }], 21, 20));
  render(view());
  await screen.findByText(session.user_agent!);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(await screen.findByText('分页暂不可用')).toBeInTheDocument();
  expect(screen.queryByText(session.user_agent!)).toBeNull();
  expect(screen.queryByText('暂无活动会话')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '刷新活动会话' }));
  expect(await screen.findByText('Recovered Browser')).toBeInTheDocument();
  expect(getActiveSessionsPage).toHaveBeenLastCalledWith(user.id, 20, 20);
});

it('returns to a valid last page after revoking its only remaining session', async () => {
  const second = { ...session, id: 'session-21', user_agent: 'Last Page Browser' };
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 21))
    .mockResolvedValueOnce(page([second], 21, 20))
    .mockResolvedValueOnce(page([], 20, 20)).mockResolvedValueOnce(page([session], 20));
  render(view());
  await screen.findByText(session.user_agent!);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText(second.user_agent!);
  fireEvent.click(screen.getByRole('button', { name: '撤销会话' }));
  const confirmation = within((await screen.findAllByRole('dialog')).at(-1)!);
  fireEvent.click(confirmation.getByRole('button', { name: '确认撤销' }));
  expect(await screen.findByText(session.user_agent!)).toBeInTheDocument();
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(3, user.id, 20, 20);
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(4, user.id, 0, 20);
  expect(screen.getByText('第 1 / 1 页 · 共 20 个会话')).toBeInTheDocument();
});

it('corrects an out-of-range page before displaying an empty state', async () => {
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 21))
    .mockResolvedValueOnce(page([], 1, 80)).mockResolvedValueOnce(page());
  const { result } = renderHook(() => useUserSessions(user.id));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => { await result.current.load(5); });
  expect(result.current.page).toBe(1);
  expect(result.current.sessions).toEqual([session]);
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(2, user.id, 80, 20);
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(3, user.id, 0, 20);
});

it('stops after one fallback when concurrent expiry invalidates the new last page', async () => {
  vi.mocked(getActiveSessionsPage).mockResolvedValueOnce(page([session], 81))
    .mockResolvedValueOnce(page([], 41, 80))
    .mockResolvedValueOnce(page([], 21, 40));
  const { result } = renderHook(() => useUserSessions(user.id));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => { await result.current.load(5); });
  expect(result.current.loading).toBe(false);
  expect(result.current.error).toBe('活动会话列表已变化，请刷新后重试');
  expect(result.current.sessions).toEqual([]);
  expect(result.current.total).toBe(0);
  expect(getActiveSessionsPage).toHaveBeenCalledTimes(3);
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(2, user.id, 80, 20);
  expect(getActiveSessionsPage).toHaveBeenNthCalledWith(3, user.id, 40, 20);
  expect(revokeUserSession).not.toHaveBeenCalled();
});

it('disables pagination and refresh while a revocation is pending', async () => {
  const pending = deferred<void>();
  vi.mocked(getActiveSessionsPage).mockResolvedValue(page([session], 21));
  vi.mocked(revokeUserSession).mockReturnValueOnce(pending.promise);
  render(view());
  const confirmation = await openConfirmation();
  fireEvent.click(confirmation.getByRole('button', { name: '确认撤销' }));
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '刷新活动会话' })).toBeDisabled();
  expect(getActiveSessionsPage).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(); });
});

it('confirms the scope before revoking every session', async () => {
  vi.mocked(revokeUserSessions).mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <RevokeUserSessionsModal target={user} currentUserId="admin-1" onClose={onClose} />
  </ConfigProvider>);
  expect(await screen.findByText(/包括已建立的 SSH 连接/)).toBeInTheDocument();
  expect(revokeUserSessions).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认撤销全部会话' }));
  await waitFor(() => expect(revokeUserSessions).toHaveBeenCalledWith(user.id));
  expect(onClose).toHaveBeenCalledOnce();
});

it('logs out the current user after all of their sessions are revoked', async () => {
  vi.mocked(revokeUserSessions).mockResolvedValue(undefined);
  const onLogout = vi.fn();
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <RevokeUserSessionsModal target={user} currentUserId={user.id}
      onClose={vi.fn()} onLogout={onLogout} />
  </ConfigProvider>);
  expect(await screen.findByText(/当前账号也会退出登录/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '确认撤销全部会话' }));
  await waitFor(() => expect(onLogout).toHaveBeenCalledOnce());
});
