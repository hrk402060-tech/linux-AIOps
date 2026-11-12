import { getDashboard } from '../api/dashboard';
import type { DashboardSnapshot } from '../types/dashboard';

export interface DashboardCacheEntry {
  snapshot: DashboardSnapshot;
  readAt: number;
}

let cached: DashboardCacheEntry | null = null;
let generation = 0;
let pending: { controller: AbortController; promise: Promise<DashboardCacheEntry> } | null = null;
const invalidationListeners = new Set<(reason: string) => void>();

export function clearDashboardCache(reason = '总览数据已重置，请重新读取'): void {
  generation += 1;
  cached = null;
  const previous = pending;
  pending = null;
  previous?.controller.abort();
  for (const notify of invalidationListeners) notify(reason);
}

export function subscribeDashboardInvalidation(listener: (reason: string) => void): () => void {
  invalidationListeners.add(listener);
  return () => { invalidationListeners.delete(listener); };
}

export function dashboardCacheGeneration(): number {
  return generation;
}

export function getCachedDashboard(): DashboardCacheEntry | null {
  return cached && Date.now() - cached.readAt < 60_000 ? cached : null;
}

export function readDashboardSnapshot(): Promise<DashboardCacheEntry> {
  if (pending) return pending.promise;
  const startedGeneration = generation;
  const controller = new AbortController();
  const promise: Promise<DashboardCacheEntry> = getDashboard(controller.signal).then((snapshot) => {
    if (startedGeneration !== generation || controller.signal.aborted) {
      throw new DOMException('已取消旧会话的数据请求', 'AbortError');
    }
    cached = { snapshot, readAt: Date.now() };
    return cached;
  }).finally(() => {
    if (pending?.promise === promise) pending = null;
  });
  pending = { controller, promise };
  return promise;
}
