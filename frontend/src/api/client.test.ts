import { afterEach, expect, it, vi } from 'vitest';
import { apiFetch } from './client';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('expires an active session after an unauthorized response', async () => {
  const expire = vi.fn();
  window.addEventListener('sentinel:session-expired', expire);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 401 }));
  await apiFetch('/api/v1/dashboard');
  expect(expire).toHaveBeenCalledOnce();
  window.removeEventListener('sentinel:session-expired', expire);
});

it('does not let an aborted old request expire a newly established session', async () => {
  const expire = vi.fn();
  window.addEventListener('sentinel:session-expired', expire);
  const controller = new AbortController();
  let resolve!: (response: { status: number }) => void;
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise((done) => { resolve = done; })));
  const pending = apiFetch('/api/v1/dashboard', { signal: controller.signal });
  controller.abort();
  resolve({ status: 401 });
  await pending;
  expect(expire).not.toHaveBeenCalled();
  window.removeEventListener('sentinel:session-expired', expire);
});
