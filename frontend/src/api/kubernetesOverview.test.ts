import { afterEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesOverview, refreshKubernetesOverview } from './kubernetesOverview';
import { testKubernetesCluster } from './kubernetes';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('Kubernetes overview requests', () => {
  it('reads a saved overview with an encoded cluster id and cancellable request', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ cluster_id: 'a/b' })));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    await expect(getKubernetesOverview('a/b', controller.signal)).resolves.toEqual({ cluster_id: 'a/b' });
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/kubernetes/clusters/a%2Fb/overview');
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(options.method ?? 'GET').toBe('GET');
    expect(options.body).toBeUndefined();
    expect(options.signal).toBeInstanceOf(AbortSignal);
    controller.abort();
    expect(options.signal?.aborted).toBe(true);
  });

  it('queues one fixed refresh endpoint without any Kubernetes command or secret', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: 'run-1', merged: false }), { status: 202 }));
    vi.stubGlobal('fetch', fetch);
    await refreshKubernetesOverview('cluster-1');
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/kubernetes/clusters/cluster-1/refresh');
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST' });
    expect((fetch.mock.calls[0][1] as RequestInit).body).toBeUndefined();
  });

  it('does not reflect upstream errors and preserves authentication status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'private-token-in-upstream-body' }), { status: 403 },
    )));
    await expect(getKubernetesOverview('private')).rejects.toMatchObject({ status: 403 });
    await expect(getKubernetesOverview('private')).rejects.not.toThrow('private-token-in-upstream-body');
  });

  it('preserves the existing longer timeout for connection testing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')));
    const timeout = vi.spyOn(AbortSignal, 'timeout');
    await testKubernetesCluster('cluster-one', { reason: '测试连接' });
    expect(timeout.mock.calls.every(([milliseconds]) => milliseconds === 40_000)).toBe(true);
    expect(timeout).toHaveBeenCalled();
  });
});
