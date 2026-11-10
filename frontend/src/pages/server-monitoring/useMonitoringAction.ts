import { useCallback, useEffect, useRef, useState } from 'react';

export function useMonitoringAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lifetime = useRef({ mounted: true, busy: false, version: 0 });
  useEffect(() => {
    lifetime.current.mounted = true;
    return () => { lifetime.current.mounted = false; lifetime.current.version += 1; };
  }, []);
  const run = useCallback(async <T>(
    operation: () => Promise<T>, onSuccess: (value: T) => void,
  ): Promise<void> => {
    const state = lifetime.current;
    if (!state.mounted || state.busy) return;
    state.busy = true;
    const version = state.version;
    const current = () => state.mounted && state.version === version;
    setBusy(true); setError('');
    try {
      const value = await operation();
      if (current()) onSuccess(value);
    } catch (value) {
      if (current()) setError(value instanceof Error ? value.message : '操作失败，请刷新后重试');
    } finally {
      if (current()) { state.busy = false; setBusy(false); }
    }
  }, []);
  return { busy, error, run };
}
