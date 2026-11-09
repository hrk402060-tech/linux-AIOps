import { useCallback, useEffect, useRef, useState } from 'react';
import { getCurrentUser, type AuthUser } from '../api/auth';

const REFRESH_INTERVAL_MS = 30_000;

function changed(previous: AuthUser, next: AuthUser): boolean {
  return previous.id !== next.id
    || (previous.access_revision ?? 0) !== (next.access_revision ?? 0)
    || previous.role !== next.role
    || previous.enabled !== next.enabled
    || previous.must_change_password !== next.must_change_password;
}

export function useCurrentUser(onScopeReset: () => void = () => undefined) {
  const [currentUser, setCurrentUserState] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const mounted = useRef(true);
  const userRef = useRef<AuthUser | null>(null);
  const requestVersion = useRef(0);
  const resetRef = useRef(onScopeReset);
  resetRef.current = onScopeReset;

  const applyUser = useCallback((next: AuthUser | null) => {
    if (!mounted.current) return;
    const previous = userRef.current;
    userRef.current = next;
    if (previous && next && changed(previous, next)) resetRef.current();
    if (previous && !next) resetRef.current();
    setCurrentUserState(next);
  }, []);

  const refresh = useCallback(async () => {
    if (!mounted.current || !userRef.current) return;
    const version = ++requestVersion.current;
    try {
      const next = await getCurrentUser();
      if (version === requestVersion.current) applyUser(next);
    } catch {
      if (version === requestVersion.current) applyUser(null);
    }
  }, [applyUser]);

  useEffect(() => {
    mounted.current = true;
    const version = ++requestVersion.current;
    getCurrentUser().then((user) => {
      if (version === requestVersion.current) applyUser(user);
    }).catch(() => {
      if (version === requestVersion.current) applyUser(null);
    })
      .finally(() => { if (mounted.current) setAuthLoading(false); });
    const expire = () => {
      requestVersion.current += 1;
      userRef.current = null;
      setCurrentUserState(null);
      setAuthLoading(false);
      resetRef.current();
    };
    const onFocus = () => { void refresh(); };
    window.addEventListener('sentinel:session-expired', expire);
    window.addEventListener('focus', onFocus);
    const timer = window.setInterval(onFocus, REFRESH_INTERVAL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      window.removeEventListener('sentinel:session-expired', expire);
      window.removeEventListener('focus', onFocus);
    };
  }, [applyUser, refresh]);

  const setCurrentUser = useCallback((next: AuthUser | null) => {
    requestVersion.current += 1;
    applyUser(next);
  }, [applyUser]);

  return { currentUser, authLoading, setCurrentUser };
}
