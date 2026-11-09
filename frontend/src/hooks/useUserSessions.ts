import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from 'antd';
import { getActiveSessionsPage, logout, revokeUserSession, type AuthSession, type AuthSessionPage } from '../api/auth';

const PAGE_SIZE = 20;

function validatePage(result: AuthSessionPage, userId: string, offset: number): void {
  if (!result || !Array.isArray(result.items)
    || !Number.isSafeInteger(result.total) || result.total < 0
    || result.offset !== offset || result.limit !== PAGE_SIZE
    || result.items.length > PAGE_SIZE) {
    throw new Error('活动会话分页数据异常，请刷新重试');
  }
  if (result.items.some((item) => item.user_id !== userId)) {
    throw new Error('会话归属校验失败，请刷新重试');
  }
}

export async function logoutRevokedSession(onLogout?: () => void): Promise<void> {
  if (onLogout) { onLogout(); return; }
  try { await logout(); }
  finally { window.dispatchEvent(new Event('sentinel:session-expired')); }
}

async function readSessionPage(
  userId: string, page: number, current: () => boolean, updatePage: (page: number) => void,
): Promise<AuthSessionPage | null> {
  let result = await getActiveSessionsPage(userId, (page - 1) * PAGE_SIZE, PAGE_SIZE);
  if (!current()) return null;
  validatePage(result, userId, (page - 1) * PAGE_SIZE);
  const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
  if (page <= lastPage) return result;
  updatePage(lastPage);
  result = await getActiveSessionsPage(userId, (lastPage - 1) * PAGE_SIZE, PAGE_SIZE);
  if (!current()) return null;
  validatePage(result, userId, (lastPage - 1) * PAGE_SIZE);
  if (lastPage > Math.max(1, Math.ceil(result.total / PAGE_SIZE))) {
    throw new Error('活动会话列表已变化，请刷新后重试');
  }
  return result;
}

function useSessionPages(userId: string) {
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revokeTarget, setRevokeTarget] = useState<AuthSession | null>(null);
  const generation = useRef(0);
  const mounted = useRef(false);
  const pageRef = useRef(1);
  const load = useCallback(async (requestedPage = pageRef.current) => {
    const request = ++generation.current;
    const updatePage = (value: number) => { pageRef.current = value; setPage(value); };
    updatePage(requestedPage);
    setLoading(true); setError(''); setSessions([]); setTotal(0); setRevokeTarget(null);
    try {
      const result = await readSessionPage(
        userId, requestedPage, () => request === generation.current, updatePage,
      );
      if (!result) return;
      setSessions(result.items); setTotal(result.total);
    } catch (failure) {
      if (request === generation.current) {
        setError(failure instanceof Error ? failure.message : '活动会话加载失败，请刷新重试');
      }
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [userId]);
  useEffect(() => {
    mounted.current = true;
    void load(1);
    return () => { mounted.current = false; generation.current += 1; };
  }, [load]);
  const fail = (detail: string) => {
    setSessions([]); setTotal(0); setRevokeTarget(null); setError(detail);
  };
  return { sessions, page, total, loading, error, revokeTarget, setRevokeTarget,
    load, generation, mounted, fail };
}

export function useUserSessions(userId: string, onLogout?: () => void) {
  const { generation, mounted, fail, ...state } = useSessionPages(userId);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [messages, holder] = message.useMessage();
  const revoke = async () => {
    if (!state.revokeTarget || savingRef.current || state.loading || state.error) return;
    const target = state.revokeTarget;
    const request = generation.current;
    savingRef.current = true; setSaving(true);
    try {
      await revokeUserSession(userId, target.id);
      if (request !== generation.current) return;
      state.setRevokeTarget(null);
      messages.success('已撤销该活动会话');
      if (target.is_current) await logoutRevokedSession(onLogout);
      else await state.load();
    } catch (failure) {
      if (request === generation.current) {
        fail(`${failure instanceof Error ? failure.message : '会话撤销请求异常'}；请刷新确认会话状态后再操作`);
      }
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
    }
  };
  const changePage = (value: number) => {
    if (!savingRef.current && !state.loading && !state.error) void state.load(value);
  };
  return { ...state, pageSize: PAGE_SIZE, saving, changePage, revoke, holder };
}
