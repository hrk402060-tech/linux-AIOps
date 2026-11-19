import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesClusters, type KubernetesCluster } from '../api/kubernetes';
import { checkKubernetesChange, getHelmCharts, previewHelmChange } from '../api/kubernetesChanges';
import { KubernetesChangesPage } from './KubernetesChangesPage';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(), getKubernetesClusters: vi.fn(),
}));
vi.mock('../api/kubernetesChanges', () => ({
  checkKubernetesChange: vi.fn(), getHelmCharts: vi.fn(), previewHelmChange: vi.fn(),
}));

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

  it('previews the selected pinned Chart and clears the result when inputs change', async () => {
    const chart = { id: 'demo-1', name: 'Demo', version: '1.0.0', digest: `sha256:${'a'.repeat(64)}` };
    vi.mocked(getHelmCharts).mockResolvedValue({ enabled: true, charts: [chart] });
    vi.mocked(previewHelmChange).mockResolvedValue({
      preflight: {
        trace_id: 'trace-helm', cluster_id: target.id, cluster_revision: 5, action: 'helm',
        status: 'requires_validation', execution_available: false, current_version: 'v1.31.0',
        observed_at: null, node_count: 3, ready_nodes: 3, checks: [],
      },
      chart, release_name: 'demo', namespace: 'payments',
      resources: [{ api_version: 'v1', kind: 'ConfigMap', namespace: 'payments', name: 'demo-settings' }],
    });
    render(<KubernetesChangesPage initialCluster={target} />);
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '变更类型' }));
    fireEvent.click(await screen.findByText('Helm 部署'));
    const chartSelect = screen.getByRole('combobox', { name: '受信 Chart' });
    await waitFor(() => expect(chartSelect).toBeEnabled());
    fireEvent.mouseDown(chartSelect);
    fireEvent.click(await screen.findByText('Demo · 1.0.0'));
    fireEvent.change(screen.getByRole('textbox', { name: '命名空间' }), { target: { value: 'payments' } });
    fireEvent.change(screen.getByRole('textbox', { name: '发布名称' }), { target: { value: 'demo' } });
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    await waitFor(() => expect(previewHelmChange).toHaveBeenCalled());
    expect(await screen.findByRole('region', { name: 'Helm 资源预览' })).toBeVisible();
    expect(previewHelmChange).toHaveBeenCalledWith(target.id, 5, {
      chart_id: chart.id, chart_digest: chart.digest, namespace: 'payments', release_name: 'demo',
    }, expect.any(AbortSignal));
    expect(checkKubernetesChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: '发布名称' }), { target: { value: 'other' } });
    expect(screen.queryByRole('region', { name: 'Helm 资源预览' })).not.toBeInTheDocument();
  });
});
