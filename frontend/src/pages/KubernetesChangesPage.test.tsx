import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKubernetesClusters, type KubernetesCluster } from '../api/kubernetes';
import {
  checkKubernetesChange, decideHelmApproval, getHelmApprovals, getHelmCharts, getKubernetesChangeCapabilities,
  previewHelmChange,
  executeHelmApproval, runHelmAdmissionRehearsal, submitHelmApproval, type HelmAdmissionResult,
  type HelmApproval,
} from '../api/kubernetesChanges';
import { KubernetesChangesPage } from './KubernetesChangesPage';
import { KubernetesHelmApprovals } from './KubernetesHelmApprovals';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(), getKubernetesClusters: vi.fn(),
}));
vi.mock('../api/kubernetesChanges', () => ({
  checkKubernetesChange: vi.fn(), getHelmCharts: vi.fn(), getKubernetesChangeCapabilities: vi.fn(),
  previewHelmChange: vi.fn(),
  getHelmApprovals: vi.fn(), submitHelmApproval: vi.fn(), decideHelmApproval: vi.fn(),
  runHelmAdmissionRehearsal: vi.fn(), executeHelmApproval: vi.fn(),
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
  fireEvent.click((await screen.findAllByText('Helm 部署')).at(-1)!);
}

describe('Kubernetes change preflight', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getKubernetesClusters).mockResolvedValue({ items: [target], total: 1 });
    vi.mocked(getHelmCharts).mockResolvedValue({ enabled: true, charts: [chart] });
    vi.mocked(getKubernetesChangeCapabilities).mockResolvedValue({
      cluster_id: target.id, cluster_revision: 5, snapshot_freshness: 'fresh',
      observed_at: '2026-11-20T00:00:00Z', node_count: 3, ready_nodes: 3,
      actions: [
        { action: 'scale', label: '节点扩容', status: 'preview_only', execution_available: false, reason: '尚未配置节点池' },
        { action: 'upgrade', label: '版本升级', status: 'preview_only', execution_available: false, reason: '尚未配置兼容矩阵' },
        { action: 'backup', label: '集群备份', status: 'preview_only', execution_available: false, reason: '尚未配置备份存储' },
        { action: 'restore', label: '集群恢复', status: 'preview_only', execution_available: false, reason: '尚未登记可验证备份' },
        { action: 'helm', label: 'Helm 部署', status: 'preview_only', execution_available: false, reason: '仅支持隔离执行' },
      ],
    });
    vi.mocked(getHelmApprovals).mockResolvedValue({ items: [], total: 0 });
  });

  it('loads action capabilities for the selected cluster', async () => {
    render(<KubernetesChangesPage initialCluster={target} />);
    expect(await screen.findByRole('region', { name: '变更能力' })).toBeVisible();
    expect(screen.getByText('尚未配置节点池')).toBeVisible();
    expect(getKubernetesChangeCapabilities).toHaveBeenCalledWith(target.id, expect.any(AbortSignal));
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

describe('Helm admission rehearsal', () => {
  async function openApproval(overrides: Partial<HelmApproval>, additional: HelmApproval[] = []) {
    vi.clearAllMocks();
    const item: HelmApproval = { ...approval, status: 'approved', can_review: false, ...overrides };
    vi.mocked(getHelmApprovals).mockResolvedValue({ items: [item, ...additional], total: additional.length + 1 });
    render(<KubernetesHelmApprovals clusterId={target.id} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看审批：demo' }));
  }

  it('does not expose rehearsal on unavailable or unapproved records', async () => {
    await openApproval({ status: 'pending', can_rehearse: true });
    expect(screen.queryByRole('button', { name: '运行准入演练' })).not.toBeInTheDocument();
    expect(runHelmAdmissionRehearsal).not.toHaveBeenCalled();
  });

  it('does not expose rehearsal without the server capability', async () => {
    await openApproval({ can_rehearse: false });
    expect(screen.queryByRole('button', { name: '运行准入演练' })).not.toBeInTheDocument();
  });

  it('requires exact confirmation and displays the non-deploying result', async () => {
    await openApproval({ can_rehearse: true });
    let finish: (result: HelmAdmissionResult) => void = () => undefined;
    vi.mocked(runHelmAdmissionRehearsal).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const panel = screen.getByRole('region', { name: '准入演练' });
    const input = within(panel).getByRole('textbox', { name: '确认演练' });
    const submit = within(panel).getByRole('button', { name: '运行准入演练' });
    fireEvent.change(input, { target: { value: 'YES' } });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: ' yes ' } });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(runHelmAdmissionRehearsal).toHaveBeenCalledTimes(1);
    expect(runHelmAdmissionRehearsal).toHaveBeenCalledWith(target.id, approval.id, expect.any(AbortSignal));
    expect(input).toBeDisabled();
    finish({ status: 'passed', execution_available: false, checked: 1, total: 1, error_code: null, trace_id: 'lab-trace' });
    expect(await within(panel).findByText('准入演练通过 · 未部署')).toBeVisible();
    expect(within(panel).getByText(/lab-trace/)).toBeVisible();
    expect(input).toHaveValue('');
  });

  it.each([
    ['upstream_forbidden', '测试身份缺少所需权限'],
    ['untrusted upstream content', '准入演练未通过'],
  ])('presents rehearsal rejection using safe localized text (%s)', async (error_code, message) => {
    await openApproval({ can_rehearse: true });
    vi.mocked(runHelmAdmissionRehearsal).mockResolvedValue({
      status: 'failed', execution_available: false, checked: 0, total: 1, error_code, trace_id: 'lab-trace',
    });
    const panel = screen.getByRole('region', { name: '准入演练' });
    fireEvent.change(within(panel).getByRole('textbox', { name: '确认演练' }), { target: { value: 'yes' } });
    fireEvent.click(within(panel).getByRole('button', { name: '运行准入演练' }));
    expect(await within(panel).findByText('准入演练失败 · 未部署')).toBeVisible();
    expect(within(panel).getByText(message)).toBeVisible();
    expect(within(panel).queryByText(error_code)).not.toBeInTheDocument();
  });

  it('aborts on selection changes and never displays the previous approval response', async () => {
    const other: HelmApproval = {
      ...approval, id: 'other-approval', release_name: 'other', status: 'approved', can_rehearse: true,
    };
    await openApproval({ can_rehearse: true }, [other]);
    let finish: (result: HelmAdmissionResult) => void = () => undefined;
    vi.mocked(runHelmAdmissionRehearsal).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const panel = screen.getByRole('region', { name: '准入演练' });
    fireEvent.change(within(panel).getByRole('textbox', { name: '确认演练' }), { target: { value: 'yes' } });
    fireEvent.click(within(panel).getByRole('button', { name: '运行准入演练' }));
    const signal = vi.mocked(runHelmAdmissionRehearsal).mock.calls[0][2];

    fireEvent.click(screen.getByRole('button', { name: '查看审批：other' }));
    expect(signal?.aborted).toBe(true);
    await act(async () => finish({
      status: 'passed', execution_available: false, checked: 1, total: 1, error_code: null, trace_id: 'obsolete-trace',
    }));
    expect(screen.queryByText(/obsolete-trace/)).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '确认演练' })).toHaveValue('');
  });

  it('does not display request error details and keeps the confirmation for retry', async () => {
    await openApproval({ can_rehearse: true });
    vi.mocked(runHelmAdmissionRehearsal).mockRejectedValue(new Error('private upstream metadata'));
    const panel = screen.getByRole('region', { name: '准入演练' });
    const input = within(panel).getByRole('textbox', { name: '确认演练' });
    fireEvent.change(input, { target: { value: 'yes' } });
    fireEvent.click(within(panel).getByRole('button', { name: '运行准入演练' }));
    expect(await within(panel).findByText('准入演练请求失败，请刷新审批记录后重试')).toBeVisible();
    expect(within(panel).queryByText('private upstream metadata')).not.toBeInTheDocument();
    expect(input).toHaveValue('yes');
  });
});

describe('Helm isolated execution', () => {
  it('requires server capability and displays successful validation', async () => {
    vi.clearAllMocks();
    vi.mocked(getHelmApprovals).mockResolvedValue({
      items: [{ ...approval, status: 'approved', can_review: false, can_execute: true }],
      total: 1,
    });
    vi.mocked(executeHelmApproval).mockResolvedValue({
      trace_id: 'exec-trace', status: 'succeeded', execution_available: true,
      created: 1, validated: 1, rollback: 'not_needed', error_code: null,
    });
    render(<KubernetesHelmApprovals clusterId={target.id} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看审批：demo' }));
    const panel = screen.getByRole('region', { name: '隔离执行' });
    const input = within(panel).getByRole('textbox', { name: '确认执行' });
    const submit = within(panel).getByRole('button', { name: '执行' });
    expect(submit).toBeDisabled();
    fireEvent.change(input, { target: { value: 'yes' } });
    fireEvent.click(submit);
    expect(await within(panel).findByText('隔离执行完成')).toBeVisible();
    expect(within(panel).getByText(/exec-trace/)).toBeVisible();
    expect(executeHelmApproval).toHaveBeenCalledWith(target.id, approval.id, expect.any(AbortSignal));
  });

  it('does not show execution without server capability', async () => {
    vi.clearAllMocks();
    vi.mocked(getHelmApprovals).mockResolvedValue({
      items: [{ ...approval, status: 'approved', can_review: false, can_execute: false }],
      total: 1,
    });
    render(<KubernetesHelmApprovals clusterId={target.id} />);
    fireEvent.click(await screen.findByRole('button', { name: '查看审批：demo' }));
    expect(screen.queryByRole('region', { name: '隔离执行' })).not.toBeInTheDocument();
  });
});
