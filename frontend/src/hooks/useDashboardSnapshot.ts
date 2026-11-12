import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  clearDashboardCache, dashboardCacheGeneration, getCachedDashboard, readDashboardSnapshot,
  subscribeDashboardInvalidation,
} from './dashboardSnapshotCache';

export function useDashboardSnapshot() {
  const [entry, setEntry] = useState(getCachedDashboard);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const current = ++requestId.current;
    const generation = dashboardCacheGeneration();
    const valid = () => current === requestId.current && generation === dashboardCacheGeneration();
    setLoading(true);
    setError('');
    try {
      const next = await readDashboardSnapshot();
      if (valid()) setEntry(next);
    } catch (reason) {
      if (!valid()) return;
      if (reason instanceof ApiError && [401, 403].includes(reason.status)) {
        clearDashboardCache(reason.message);
        return;
      }
      setError(reason instanceof Error ? reason.message : '总览数据加载失败');
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const unsubscribe = subscribeDashboardInvalidation((reason) => {
      requestId.current += 1;
      setEntry(null);
      setError(reason);
      setLoading(false);
    });
    setEntry(getCachedDashboard());
    void load();
    const expire = () => clearDashboardCache('会话已失效，请重新登录');
    window.addEventListener('sentinel:session-expired', expire);
    return () => {
      requestId.current += 1;
      unsubscribe();
      window.removeEventListener('sentinel:session-expired', expire);
    };
  }, [load]);

  return { entry, loading, error, load };
}
