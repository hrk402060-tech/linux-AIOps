import { useEffect, useState } from 'react';

interface Result<T> { key: string; data: T | null; error: string; loading: boolean }

export function useMonitoringRequest<T>(key: string, request: (signal: AbortSignal) => Promise<T>) {
  const [result, setResult] = useState<Result<T>>({ key: '', data: null, error: '', loading: true });
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setResult({ key, data: null, error: '', loading: true });
    void request(controller.signal).then(
      (data) => { if (current) setResult({ key, data, error: '', loading: false }); },
      (error: unknown) => {
        if (current) setResult({ key, data: null, loading: false,
          error: error instanceof Error ? error.message : '监控请求失败，请重试' });
      },
    );
    return () => { current = false; controller.abort(); };
  }, [key, request]);
  return result.key === key ? result : { key, data: null, error: '', loading: true };
}
