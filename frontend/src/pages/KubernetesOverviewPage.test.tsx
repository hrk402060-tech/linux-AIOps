import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesClusters, type KubernetesCluster } from '../api/kubernetes';
import {
  getKubernetesOverview, refreshKubernetesOverview, type KubernetesOverview,
} from '../api/kubernetesOverview';
import { KubernetesOverviewPage } from './KubernetesOverviewPage';

vi.mock('../api/kubernetes', async (original) => ({
  ...await original<typeof import('../api/kubernetes')>(),
  getKubernetesClusters: vi.fn(), updateKubernetesCluster: vi.fn(),
}));
vi.mock('../api/kubernetesOverview', async (original) => ({
  ...await original<typeof import('../api/kubernetesOverview')>(),
  getKubernetesOverview: vi.fn(), refreshKubernetesOverview: vi.fn(),
}));
const cluster = {
  id: 'cluster-one', name: '生产集群', lifecycle: 'active', revision: 1,
  collection_interval_seconds: 60, version: 'v1.31.2',
} as KubernetesCluster;
const completeResource = { state: 'complete', observed_count: 2, total: 2, error_code: null } as const;
const overview: KubernetesOverview = {
  cluster_id: cluster.id, collection_state: 'complete', freshness: 'fresh', run_id: 'run-one',
  observed_at: '2026-11-17T01:00:00Z', last_success_at: '2026-11-17T01:00:00Z',
  counts: { nodes: 2, nodes_ready: 1, nodes_not_ready: 1, namespaces: 4, pods: 12, pods_abnormal: 2,
    deployments: 3, statefulsets: 1, daemonsets: 2, services: 7, warning_events: 1 },
  resources: Object.fromEntries(
    ['nodes', 'namespaces', 'pods', 'deployments', 'statefulsets', 'daemonsets', 'services', 'events']
      .map((kind) => [kind, completeResource]),
  ),
  metrics: { state: 'partial', cpu_cores: 0.25, cpu_allocatable_cores: 2, cpu_percent: 12.5,
    memory_bytes: 1073741824, memory_allocatable_bytes: 2147483648, memory_percent: 50,
    covered_nodes: 1, total_nodes: 2, sample_time: '2026-11-17T01:00:00Z', window: '30s',
    source: 'metrics.k8s.io', error_code: null },
  warnings: [{ reason: 'BackOff', target: 'Pod/api-123', namespace: 'production', count: 3, last_seen: '2026-11-17T01:00:00Z' }],
  errors: [],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => { resolve = finish; });
  return { promise, resolve };
}
function show(role: 'admin' | 'operator' | 'auditor' = 'admin') {
  return render(<ConfigProvider theme={{ token: { motion: false } }}>
    <KubernetesOverviewPage currentUser={{ role }} onOpenConnections={vi.fn()} />
  </ConfigProvider>);
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getKubernetesClusters).mockResolvedValue({ total: 1, items: [cluster] });
  vi.mocked(getKubernetesOverview).mockResolvedValue(overview);
  vi.mocked(refreshKubernetesOverview).mockResolvedValue({
    cluster_id: cluster.id, run_id: 'run-two', state: 'queued', merged: false,
  });
});
afterEach(() => vi.useRealTimers());

describe('KubernetesOverviewPage', () => {
  it.each(['operator', 'auditor'] as const)('does not query cluster data for %s', (role) => {
    show(role);
    expect(screen.getByText('当前阶段仅管理员可查看 Kubernetes 集群总览。')).toBeInTheDocument();
    expect(getKubernetesClusters).not.toHaveBeenCalled();
    expect(getKubernetesOverview).not.toHaveBeenCalled();
  });

  it('shows unknown values while the actual overview is loading', async () => {
    const pending = deferred<KubernetesOverview>();
    vi.mocked(getKubernetesOverview).mockReturnValue(pending.promise);
    show();
    await waitFor(() => expect(getKubernetesOverview).toHaveBeenCalled());
    expect(screen.getAllByText('—').length).toBeGreaterThan(5);
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    await act(async () => pending.resolve(overview));
    expect(await screen.findByText('12.5%')).toBeInTheDocument();
  });

  it('shows real counts, abnormal status, metric coverage and safe event fields', async () => {
    show();
    expect(await screen.findByText('12.5%')).toBeInTheDocument();
    expect(screen.getByText('50.0%')).toBeInTheDocument();
    expect(screen.getByText('1 / 2 个节点')).toBeInTheDocument();
    expect(screen.getByText('metrics.k8s.io')).toBeInTheDocument();
    expect(screen.getByText('BackOff')).toBeInTheDocument();
    expect(screen.getByText('Pod/api-123')).toBeInTheDocument();
    expect(screen.getByText('节点异常')).toBeInTheDocument();
    expect(refreshKubernetesOverview).not.toHaveBeenCalled();
  });

  it('keeps failed, partial and stale data distinct from healthy zero values', async () => {
    vi.mocked(getKubernetesOverview).mockResolvedValue({
      ...overview, collection_state: 'partial', freshness: 'stale',
      counts: { ...overview.counts, pods: null, pods_abnormal: null },
      resources: { ...overview.resources, pods: { state: 'partial', observed_count: 5, total: null, error_code: 'item_limit' } },
      metrics: { ...overview.metrics, state: 'unavailable', cpu_percent: null, memory_percent: null, covered_nodes: 0 },
      errors: [{ resource: 'pods', code: 'item_limit', message: 'unsafe-private-response' }],
    });
    show();
    expect(await screen.findByText('数据已过期')).toBeInTheDocument();
    expect(screen.getAllByText('部分成功').length).toBeGreaterThan(0);
    expect(screen.getByText('已观测 5 条')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText('unsafe-private-response')).not.toBeInTheDocument();
  });

  it('binds results to the selected cluster and discards an old late response', async () => {
    const pending = deferred<KubernetesOverview>();
    vi.mocked(getKubernetesClusters).mockResolvedValue({
      total: 2, items: [cluster, { ...cluster, id: 'cluster-two', name: '研发集群' }],
    });
    vi.mocked(getKubernetesOverview).mockImplementation((id) =>
      id === cluster.id ? pending.promise : Promise.resolve({ ...overview, cluster_id: id, counts: { ...overview.counts, nodes: 8 } }));
    show();
    await waitFor(() => expect(getKubernetesOverview).toHaveBeenCalledWith(cluster.id, expect.any(AbortSignal)));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择 Kubernetes 集群' }));
    fireEvent.click(await screen.findByText('研发集群', { selector: '.ant-select-item-option-content' }));
    await waitFor(() => expect(getKubernetesOverview).toHaveBeenCalledWith('cluster-two', expect.any(AbortSignal)));
    await act(async () => pending.resolve(overview));
    expect(screen.getByTestId('k8s-count-nodes')).toHaveTextContent('8');
    expect(screen.queryByText('正在加载')).not.toBeInTheDocument();
  });

  it('queues a refresh once despite repeated clicks and follows the same cluster', async () => {
    const pending = deferred<Awaited<ReturnType<typeof refreshKubernetesOverview>>>();
    vi.mocked(refreshKubernetesOverview).mockReturnValue(pending.promise);
    show(); await screen.findByText('12.5%');
    const refresh = screen.getByRole('button', { name: '采集最新数据' });
    fireEvent.click(refresh); fireEvent.click(refresh);
    expect(refreshKubernetesOverview).toHaveBeenCalledTimes(1);
    expect(refreshKubernetesOverview).toHaveBeenCalledWith(cluster.id, expect.any(AbortSignal));
    await act(async () => pending.resolve({
      cluster_id: cluster.id, run_id: 'run-two', state: 'queued', merged: true,
    }));
    await waitFor(() => expect(refresh).toBeEnabled());
  });

  it('does not show a fake empty fleet when loading fails', async () => {
    vi.mocked(getKubernetesClusters).mockRejectedValue(new Error('private details'));
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent('集群请求失败');
    expect(screen.queryByText('暂无已接入集群')).not.toBeInTheDocument();
  });

  it('clears the selected overview when a search returns no matching clusters', async () => {
    vi.mocked(getKubernetesClusters).mockImplementation(async (params) =>
      params?.search ? { total: 0, items: [] } : { total: 1, items: [cluster] });
    show();
    await screen.findByText('12.5%');
    const search = screen.getByRole('searchbox', { name: '搜索总览集群' });
    fireEvent.change(search, { target: { value: '不存在的集群' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    expect(await screen.findByText('暂无匹配集群')).toBeInTheDocument();
    expect(screen.queryByText('12.5%')).not.toBeInTheDocument();
  });

  it('aborts an overview read on unmount and does not show it after permission loss', async () => {
    const pending = deferred<KubernetesOverview>();
    vi.mocked(getKubernetesOverview).mockReturnValue(pending.promise);
    const rendered = show();
    await waitFor(() => expect(getKubernetesOverview).toHaveBeenCalled());
    const signal = vi.mocked(getKubernetesOverview).mock.calls[0][1];
    rendered.rerender(<KubernetesOverviewPage currentUser={{ role: 'auditor' }} onOpenConnections={vi.fn()} />);
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(overview));
    expect(screen.queryByText('12.5%')).not.toBeInTheDocument();
    expect(screen.getByText('当前阶段仅管理员可查看 Kubernetes 集群总览。')).toBeInTheDocument();
  });
});
