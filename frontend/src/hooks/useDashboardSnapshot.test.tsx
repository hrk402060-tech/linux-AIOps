import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { getDashboard } from '../api/dashboard';
import { dashboardFixture } from '../test/dashboardFixture';
import { clearDashboardCache, getCachedDashboard } from './dashboardSnapshotCache';
import { useDashboardSnapshot } from './useDashboardSnapshot';

vi.mock('../api/dashboard', () => ({ getDashboard: vi.fn() }));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
beforeEach(() => {
  clearDashboardCache();
  vi.mocked(getDashboard).mockReset().mockResolvedValue(dashboardFixture);
});
afterEach(() => { clearDashboardCache(); vi.restoreAllMocks(); });

it('starts without invented statistics and stores only successful snapshots', async () => {
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  const { result } = renderHook(useDashboardSnapshot);
  expect(result.current.entry).toBeNull();
  expect(result.current.loading).toBe(true);
  await act(async () => pending.resolve(dashboardFixture));
  expect(result.current.entry?.snapshot.assets.total).toBe(4);
  expect(result.current.loading).toBe(false);
});

it('renders the last snapshot immediately on returning while updating in the background', async () => {
  const first = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(first.result.current.loading).toBe(false));
  first.unmount();
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  const second = renderHook(useDashboardSnapshot);
  expect(second.result.current.entry?.snapshot.assets.total).toBe(4);
  expect(second.result.current.loading).toBe(true);
  await act(async () => pending.resolve({ ...dashboardFixture, assets: { ...dashboardFixture.assets, total: 5 } }));
  expect(second.result.current.entry?.snapshot.assets.total).toBe(5);
  expect(getDashboard).toHaveBeenCalledTimes(2);
});

it('retains current data during refresh and shows failures without extending its successful read time', async () => {
  const view = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  const readAt = view.result.current.entry?.readAt;
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  act(() => { void view.result.current.load(); });
  expect(view.result.current.entry?.snapshot.assets.total).toBe(4);
  await act(async () => pending.reject(new Error('网络暂不可用')));
  expect(view.result.current.error).toBe('网络暂不可用');
  expect(view.result.current.entry?.readAt).toBe(readAt);
  expect(view.result.current.loading).toBe(false);
});

it('does not reuse cached snapshots older than sixty seconds', async () => {
  const first = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(first.result.current.loading).toBe(false));
  const now = first.result.current.entry!.readAt;
  first.unmount();
  vi.spyOn(Date, 'now').mockReturnValue(now + 60_001);
  vi.mocked(getDashboard).mockReturnValue(new Promise(() => undefined));
  const second = renderHook(useDashboardSnapshot);
  expect(second.result.current.entry).toBeNull();
});

it('deduplicates requests shared by concurrently mounted consumers', async () => {
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  const first = renderHook(useDashboardSnapshot);
  const second = renderHook(useDashboardSnapshot);
  expect(getDashboard).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(dashboardFixture));
  expect(first.result.current.entry?.snapshot.assets.total).toBe(4);
  expect(second.result.current.entry?.snapshot.assets.total).toBe(4);
});

it.each([401, 403])('clears visible and cached data after a current %s response', async (status) => {
  const view = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  vi.mocked(getDashboard).mockRejectedValue(new ApiError('权限已失效', status));
  await act(async () => view.result.current.load());
  expect(view.result.current.entry).toBeNull();
  expect(getCachedDashboard()).toBeNull();
  expect(view.result.current.loading).toBe(false);
});

it('clears every mounted consumer after a shared forbidden response', async () => {
  const first = renderHook(useDashboardSnapshot);
  const second = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(second.result.current.loading).toBe(false));
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  act(() => {
    void first.result.current.load();
    void second.result.current.load();
  });
  await act(async () => pending.reject(new ApiError('权限已失效', 403)));
  for (const view of [first, second]) {
    expect(view.result.current.entry).toBeNull();
    expect(view.result.current.error).toBe('权限已失效');
    expect(view.result.current.loading).toBe(false);
  }
});

it('immediately clears mounted content when the authentication scope resets', async () => {
  const view = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(view.result.current.loading).toBe(false));
  act(() => clearDashboardCache());
  expect(view.result.current.entry).toBeNull();
  expect(view.result.current.loading).toBe(false);
});

it('aborts the old scope and prevents late success from replacing the new scope', async () => {
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValueOnce(pending.promise);
  const first = renderHook(useDashboardSnapshot);
  const signal = vi.mocked(getDashboard).mock.calls[0][0];
  first.unmount();
  clearDashboardCache();
  expect(signal?.aborted).toBe(true);
  const second = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(second.result.current.loading).toBe(false));
  await act(async () => pending.resolve({ ...dashboardFixture, assets: { ...dashboardFixture.assets, total: 999 } }));
  expect(getCachedDashboard()?.snapshot.assets.total).toBe(4);
});

it('does not let an old scope failure clear a new scope snapshot', async () => {
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValueOnce(pending.promise);
  const first = renderHook(useDashboardSnapshot);
  first.unmount();
  clearDashboardCache();
  const second = renderHook(useDashboardSnapshot);
  await waitFor(() => expect(second.result.current.loading).toBe(false));
  await act(async () => pending.reject(new ApiError('旧权限已撤销', 403)));
  expect(getCachedDashboard()?.snapshot.assets.total).toBe(4);
});

it('discards pending responses after session expiry', async () => {
  const pending = deferred<typeof dashboardFixture>();
  vi.mocked(getDashboard).mockReturnValue(pending.promise);
  const view = renderHook(useDashboardSnapshot);
  act(() => window.dispatchEvent(new Event('sentinel:session-expired')));
  await act(async () => pending.resolve(dashboardFixture));
  expect(view.result.current.entry).toBeNull();
  expect(view.result.current.error).toContain('会话已失效');
  expect(getCachedDashboard()).toBeNull();
});
