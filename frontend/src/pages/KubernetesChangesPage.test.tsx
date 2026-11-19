import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesClusters, type KubernetesCluster } from '../api/kubernetes';
import {
  checkKubernetesChange, decideHelmApproval, getHelmApprovals, getHelmCharts, previewHelmChange,
  submitHelmApproval, type HelmApproval,
} from '../api/kubernetesChanges';
import { KubernetesChangesPage } from './KubernetesChangesPage';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(), getKubernetesClusters: vi.fn(),
}));
vi.mock('../api/kubernetesChanges', () => ({
  checkKubernetesChange: vi.fn(), getHelmCharts: vi.fn(), previewHelmChange: vi.fn(),
  getHelmApprovals: vi.fn(), submitHelmApproval: vi.fn(), decideHelmApproval: vi.fn(),
}));

const target = { id: 'test-cluster', name: '测试集群', revision: 5 } as KubernetesCluster;
const chart = { id: 'demo-1', name: 'Demo', version: '1.0.0', digest: `sha256:${'a'.repeat(64)}` };
const approval: HelmApproval = {
  id: 'trace-helm', cluster_revision: 5, chart, release_name: 'demo', namespace: 'payments',
  resources: [{ api_version: 'v1', kind: 'ConfigMap', namespace: 'payments', name: 'demo-settings' }],
  requested_by: 'operator-1', reviewed_by: null, reason: '发布值班设置',
  submitted_at: '2026-11-19T12:00:00Z', reviewed_at: null, expires_at: '2026-11-19T12:30:00Z',
  execution_available: false, status: 'pending', can_review: true,
};

async function openHelmPage() {
  render(<KubernetesChangesPage initialCluster={target} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '运行预检' })).toBeEnabled());
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '变更类型' }));
  fireEvent.click(await screen.findByText('Helm 部署'));
}

describe('Kubernetes change preflight', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getKubernetesClusters).mockResolvedValue({ items: [target], total: 1 });
    vi.mocked(getHelmCharts).mockResolvedValue({ enabled: true, charts: [chart] });
    vi.mocked(getHelmApprovals).mockResolvedValue({ items: [], total: 0 });
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

  it('submits an exact pinned preview and clears the result when inputs change', async () => {
    vi.mocked(previewHelmChange).mockResolvedValue({
      preflight: {
        trace_id: 'trace-helm', cluster_id: target.id, cluster_revision: 5, action: 'helm',
        status: 'requires_validation', execution_available: false, current_version: 'v1.31.0',
        observed_at: null, node_count: 3, ready_nodes: 3, checks: [],
      },
      chart, release_name: 'demo', namespace: 'payments',
      resources: approval.resources, preview_id: 'trace-helm', preview_expires_at: approval.expires_at,
    });
    vi.mocked(submitHelmApproval).mockImplementation(async () => {
      const ownRequest = { ...approval, can_review: false };
      vi.mocked(getHelmApprovals).mockResolvedValue({ items: [ownRequest], total: 1 });
      return ownRequest;
    });
    await openHelmPage();
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
    const request = screen.getByRole('button', { name: '提交审批' });
    expect(request).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: '申请原因' }), { target: { value: '  发布值班设置  ' } });
    fireEvent.click(request);
    expect(await screen.findByText('待审批')).toBeVisible();
    expect(submitHelmApproval).toHaveBeenCalledWith(target.id, 'trace-helm', '发布值班设置', expect.any(AbortSignal));
    expect(screen.queryByRole('button', { name: '提交审批' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看审批：demo' }));
    expect(screen.queryByRole('textbox', { name: '确认意见' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '发布名称' }), { target: { value: 'other' } });
    expect(screen.queryByRole('region', { name: 'Helm 资源预览' })).not.toBeInTheDocument();
  });

  it.each([[' yes ', 'yes', 'approved', '已通过 · 未执行'], ['no', 'no', 'rejected', '已拒绝'] ] as const)(
    'requires exact confirmation before recording %s', async (typed, confirmation, status, label) => {
      vi.mocked(getHelmApprovals).mockResolvedValue({ items: [approval], total: 1 });
      vi.mocked(decideHelmApproval).mockImplementation(async () => {
        const reviewed = { ...approval, status, can_review: false, reviewed_by: 'reviewer-2' };
        vi.mocked(getHelmApprovals).mockResolvedValue({ items: [reviewed], total: 1 });
        return reviewed;
      });
      await openHelmPage();
      fireEvent.click(await screen.findByRole('button', { name: '查看审批：demo' }));
      const details = screen.getByRole('region', { name: '审批详情' });
      expect(within(details).getByText('payments/demo-settings')).toBeVisible();
      const input = within(details).getByRole('textbox', { name: '确认意见' });
      const submit = within(details).getByRole('button', { name: '确认' });
      fireEvent.change(input, { target: { value: 'YES' } });
      expect(submit).toBeDisabled();
      fireEvent.change(input, { target: { value: typed } });
      fireEvent.click(submit);
      expect(await screen.findByText(label)).toBeVisible();
      expect(decideHelmApproval).toHaveBeenCalledWith(target.id, approval.id, confirmation, expect.any(AbortSignal));
      expect(within(details).queryByRole('textbox', { name: '确认意见' })).not.toBeInTheDocument();
    },
  );
});
