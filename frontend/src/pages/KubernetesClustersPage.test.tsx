import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import {
  createKubernetesCluster, getKubernetesClusters, rotateKubernetesClusterCredential,
  testKubernetesCluster, testKubernetesClusterDraft, updateKubernetesCluster,
  type KubernetesCluster, type KubernetesClusterList,
} from '../api/kubernetes';
import { KubernetesClustersPage } from './KubernetesClustersPage';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(),
  createKubernetesCluster: vi.fn(), getKubernetesClusters: vi.fn(),
  rotateKubernetesClusterCredential: vi.fn(), testKubernetesCluster: vi.fn(),
  testKubernetesClusterDraft: vi.fn(), updateKubernetesCluster: vi.fn(),
}));
const cluster: KubernetesCluster = {
  id: 'cluster-1', name: '生产集群', endpoint: 'https://192.0.2.10:6443', context: 'prod',
  service_id: 'service-1', lifecycle: 'active', connection_status: 'failed',
  authorization_status: 'verified', version: 'v1.31.2', capabilities: { fixed_resource_read: true },
  collection_interval_seconds: 60, identity_hint: 'ops-reader',
  server_cert_expires_at: '2026-12-20T00:00:00Z', ca_cert_expires_at: null,
  client_cert_expires_at: null, credential_revision: 3, revision: 4,
  last_tested_at: '2026-11-16T01:00:00Z', last_success_at: null,
  error_code: 'tls_failed', error_message: 'unsafe-upstream-secret', secret_configured: true,
  updated_at: '2026-11-16T01:00:00Z',
};
function show(role: 'admin' | 'operator' | 'auditor' = 'admin') {
  return render(<ConfigProvider theme={{ token: { motion: false } }}>
    <KubernetesClustersPage currentUser={{ role }} />
  </ConfigProvider>);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => { resolve = finish; });
  return { promise, resolve };
}
function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getKubernetesClusters).mockResolvedValue({ total: 1, items: [cluster] });
  vi.mocked(createKubernetesCluster).mockResolvedValue(cluster);
  vi.mocked(testKubernetesClusterDraft).mockResolvedValue({ status: 'passed', test_id: 'valid-test-token', version: 'v1.31.2' });
  vi.mocked(updateKubernetesCluster).mockResolvedValue({ ...cluster, revision: 5 });
  vi.mocked(rotateKubernetesClusterCredential).mockResolvedValue({ ...cluster, revision: 5 });
  vi.mocked(testKubernetesCluster).mockResolvedValue({ status: 'passed', version: 'v1.31.2' });
});

describe('KubernetesClustersPage', () => {
  it('shows tested fields and safe Chinese failure reasons without instructional sections', async () => {
    show();
    expect(await screen.findByText(cluster.name)).toBeInTheDocument();
    expect(screen.getByText('v1.31.2')).toBeInTheDocument();
    expect(screen.getByText('连接失败')).toBeInTheDocument();
    expect(screen.getByText('已授权')).toBeInTheDocument();
    expect(screen.getByText('服务端证书校验失败，请检查证书链和主机名')).toBeInTheDocument();
    expect(screen.queryByText('unsafe-upstream-secret')).not.toBeInTheDocument();
    expect(screen.queryByText(/仅管理集群|浏览器表单立即清空|集群总览将在/)).not.toBeInTheDocument();
  });

  it('requests later pages and resets offset when search or lifecycle filters change', async () => {
    vi.mocked(getKubernetesClusters).mockImplementation(async (params) => {
      if (params?.status === 'archived') return { total: 1, items: [{ ...cluster, name: '已归档集群', lifecycle: 'archived' }] };
      return { total: 101, items: [{ ...cluster, name: params?.offset === 50 ? '第二页集群' : cluster.name }] };
    });
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByTitle('2'));
    expect(await screen.findByText('第二页集群')).toBeInTheDocument();
    expect(getKubernetesClusters).toHaveBeenLastCalledWith({ offset: 50, limit: 50, search: '', status: undefined });
    const search = screen.getByRole('searchbox', { name: '搜索 Kubernetes 集群' });
    fireEvent.change(search, { target: { value: 'prod' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(getKubernetesClusters).toHaveBeenLastCalledWith({ offset: 0, limit: 50, search: 'prod', status: undefined }));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '筛选集群状态' }));
    fireEvent.click(await screen.findByText('已归档', { selector: '.ant-select-item-option-content' }));
    expect(await screen.findByText('已归档集群')).toBeInTheDocument();
    expect(getKubernetesClusters).toHaveBeenLastCalledWith({ offset: 0, limit: 50, search: 'prod', status: 'archived' });
  });

  it('does not apply a stale list response over newer search results', async () => {
    const old = deferred<KubernetesClusterList>();
    vi.mocked(getKubernetesClusters).mockReturnValueOnce(old.promise);
    show();
    const search = screen.getByRole('searchbox', { name: '搜索 Kubernetes 集群' });
    fireEvent.change(search, { target: { value: 'new' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await screen.findByText(cluster.name);
    await act(async () => { old.resolve({ total: 1, items: [{ ...cluster, name: '迟到集群' }] }); });
    expect(screen.queryByText('迟到集群')).not.toBeInTheDocument();
    expect(screen.getByText(cluster.name)).toBeInTheDocument();
  });

  it('edits metadata in one PATCH and clears a linked service with null', async () => {
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: `编辑 ${cluster.name}` }));
    const dialog = within(screen.getByRole('dialog', { name: '编辑 Kubernetes 集群' }));
    expect(dialog.queryByText(/API Server|kubeconfig|采集周期|关闭自动采集/)).not.toBeInTheDocument();
    fill('关联业务服务 ID', '');
    fireEvent.click(dialog.getByRole('button', { name: '保存配置' }));
    await waitFor(() => expect(updateKubernetesCluster).toHaveBeenCalledWith(cluster.id, {
      name: cluster.name, service_id: null, expected_revision: 4,
    }));
    expect(rotateKubernetesClusterCredential).not.toHaveBeenCalled();
  });

  it('rotates tested credentials in one PUT and never follows it with a metadata PATCH', async () => {
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: `轮换 ${cluster.name} 凭据` }));
    fill('kubeconfig', 'new-private-kubeconfig'); fill('轮换原因', '到期轮换凭据');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存新凭据' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存新凭据' }));
    await waitFor(() => expect(rotateKubernetesClusterCredential).toHaveBeenCalledWith(cluster.id, {
      kubeconfig: 'new-private-kubeconfig', context: 'prod', endpoint: cluster.endpoint,
      reason: '到期轮换凭据', expected_revision: 4, test_id: 'valid-test-token',
    }));
    expect(updateKubernetesCluster).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '轮换 Kubernetes 凭据' })).not.toBeInTheDocument());
  });

  it('reopens a conflicted rotation with cleared credentials and the refreshed revision', async () => {
    vi.mocked(getKubernetesClusters)
      .mockResolvedValue({ total: 1, items: [{ ...cluster, revision: 5 }] })
      .mockResolvedValueOnce({ total: 1, items: [cluster] });
    vi.mocked(rotateKubernetesClusterCredential)
      .mockRejectedValueOnce(new ApiError('集群配置已变化，请刷新后重试', 409))
      .mockResolvedValue({ ...cluster, revision: 6 });
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: `轮换 ${cluster.name} 凭据` }));
    fill('kubeconfig', 'new-private-kubeconfig'); fill('轮换原因', '到期轮换凭据');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存新凭据' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存新凭据' }));
    expect(await screen.findByText('集群配置已变化，请刷新后重试')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '轮换 Kubernetes 凭据' })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('button', { name: `轮换 ${cluster.name} 凭据` })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: `轮换 ${cluster.name} 凭据` }));
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
    expect(screen.getByRole('button', { name: '保存新凭据' })).toBeDisabled();
    fill('kubeconfig', 'retested-private-kubeconfig'); fill('轮换原因', '冲突后重新轮换');
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存新凭据' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '保存新凭据' }));
    await waitFor(() => expect(rotateKubernetesClusterCredential).toHaveBeenLastCalledWith(cluster.id, {
      kubeconfig: 'retested-private-kubeconfig', context: 'prod', endpoint: cluster.endpoint,
      reason: '冲突后重新轮换', expected_revision: 5, test_id: 'valid-test-token',
    }));
    expect(updateKubernetesCluster).not.toHaveBeenCalled();
  });

  it('sends lifecycle changes with a reason and the current revision', async () => {
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: `停用 ${cluster.name}` }));
    fill('操作原因', '停止集群接入');
    fireEvent.click(screen.getByRole('button', { name: '确认停用' }));
    await waitFor(() => expect(updateKubernetesCluster).toHaveBeenCalledWith(cluster.id, {
      lifecycle: 'disabled', expected_revision: 4, reason: '停止集群接入',
    }));
  });

  it('keeps the saved test failure visible after reloading the list', async () => {
    vi.mocked(testKubernetesCluster).mockResolvedValue({ status: 'failed', error_code: 'authentication_failed' });
    show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: `测试 ${cluster.name}` }));
    fill('操作原因', '检查只读连接');
    fireEvent.click(screen.getByRole('button', { name: '开始测试' }));
    expect(await screen.findByText('集群认证失败，请更新凭据')).toBeInTheDocument();
    expect(testKubernetesCluster).toHaveBeenCalledWith(cluster.id, { reason: '检查只读连接' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('集群认证失败，请更新凭据')).toBeInTheDocument();
  });

  it.each(['operator', 'auditor'] as const)('rejects %s access without making cluster requests', (role) => {
    show(role);
    expect(screen.getByText('当前阶段仅管理员可查看和管理 Kubernetes 集群接入。')).toBeInTheDocument();
    expect(getKubernetesClusters).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '接入集群' })).not.toBeInTheDocument();
  });

  it('closes secret-bearing forms when administrator access is lost', async () => {
    const view = show(); await screen.findByText(cluster.name);
    fireEvent.click(screen.getByRole('button', { name: '接入集群' }));
    fill('kubeconfig', 'private-in-browser');
    view.rerender(<KubernetesClustersPage currentUser={{ role: 'auditor' }} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('kubeconfig')).not.toBeInTheDocument();
  });
});
