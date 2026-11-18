import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesClusters, type KubernetesCluster } from '../api/kubernetes';
import { checkKubernetesChange } from '../api/kubernetesChanges';
import { KubernetesChangesPage } from './KubernetesChangesPage';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(), getKubernetesClusters: vi.fn(),
}));
vi.mock('../api/kubernetesChanges', () => ({ checkKubernetesChange: vi.fn() }));

const target = { id: 'test-cluster', name: '测试集群', revision: 5 } as KubernetesCluster;

describe('Kubernetes change preflight', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getKubernetesClusters).mockResolvedValue({ items: [target], total: 1 });
  });

  it('requires a selected cluster', async () => {
    render(<KubernetesChangesPage />);
    await waitFor(() => expect(screen.getByRole('combobox', { name: '目标集群' })).toBeEnabled());
    expect(screen.getByRole('button', { name: '运行预检' })).toBeDisabled();
    expect(checkKubernetesChange).not.toHaveBeenCalled();
  });

  it('submits the current revision and displays the authoritative blocked result', async () => {
    vi.mocked(checkKubernetesChange).mockResolvedValue({
      trace_id: 'trace-1', cluster_id: target.id, cluster_revision: 5, action: 'scale',
      status: 'blocked', execution_available: false, current_version: 'v1.31.0',
      observed_at: null, node_count: null, ready_nodes: null,
      checks: [{ code: 'snapshot', label: '集群快照完整且未过期', status: 'blocked' }],
    });
    render(<KubernetesChangesPage initialCluster={target} />);
    const submit = screen.getByRole('button', { name: '运行预检' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(screen.getByRole('spinbutton', { name: '新增节点数' }), { target: { value: '2' } });
    fireEvent.click(submit);
    expect(await screen.findByText('预检受阻 · 未执行')).toBeVisible();
    expect(screen.getByText('无有效快照')).toBeVisible();
    expect(checkKubernetesChange).toHaveBeenCalledWith(target.id, 5, { action: 'scale', additional_nodes: 2 },
      expect.any(AbortSignal));
    fireEvent.change(screen.getByRole('spinbutton', { name: '新增节点数' }), { target: { value: '3' } });
    expect(screen.queryByRole('region', { name: '预检结果' })).not.toBeInTheDocument();
  });
});
