import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { testKubernetesClusterDraft, type KubernetesCluster, type KubernetesClusterTestResult } from '../api/kubernetes';
import { KubernetesConnectionForm, KubernetesMetadataForm } from './KubernetesClusterForms';

vi.mock('../api/kubernetes', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/kubernetes')>(), testKubernetesClusterDraft: vi.fn(),
}));
const cluster: KubernetesCluster = {
  id: 'cluster-1', name: '生产集群', endpoint: 'https://192.0.2.10:6443', context: 'prod',
  service_id: 'service-1', lifecycle: 'active', connection_status: 'connected', authorization_status: 'verified',
  version: 'v1.31.2', capabilities: { fixed_resource_read: true }, collection_interval_seconds: 60,
  identity_hint: 'ops-reader', server_cert_expires_at: null, ca_cert_expires_at: null, client_cert_expires_at: null,
  credential_revision: 3, revision: 4, last_tested_at: null, last_success_at: null, error_code: null,
  error_message: null, secret_configured: true, updated_at: '2026-11-16T01:00:00Z',
};
const passed: KubernetesClusterTestResult = { status: 'passed', test_id: 'valid-test-token', version: 'v1.31.2' };
const kubeconfig = 'apiVersion: v1\nkind: Config\nstatic-sensitive-value';
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => { resolve = finish; });
  return { promise, resolve };
}
function show(target: KubernetesCluster | null = null) {
  const onCreate = vi.fn().mockResolvedValue(undefined);
  const onRotate = vi.fn().mockResolvedValue(undefined);
  const result = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <KubernetesConnectionForm cluster={target} busy={false} onCancel={vi.fn()} onCreate={onCreate} onRotate={onRotate} />
  </ConfigProvider>);
  return { ...result, onCreate, onRotate };
}
function enterConfig() {
  fireEvent.change(screen.getByLabelText('kubeconfig context'), { target: { value: 'prod' } });
  fireEvent.change(screen.getByLabelText('kubeconfig'), { target: { value: kubeconfig } });
}
async function passTest() {
  fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
  await waitFor(() => expect(screen.getByRole('button', { name: /接入并保存|保存新凭据/ })).toBeEnabled());
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(testKubernetesClusterDraft).mockResolvedValue(passed);
});

describe('Kubernetes connection proof', () => {
  it('subscribes to context and kubeconfig, requires a test, and clears the secret on submit', async () => {
    const { onCreate } = show();
    fireEvent.change(screen.getByLabelText('集群名称'), { target: { value: '新集群' } });
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
    enterConfig();
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
    await passTest();
    fireEvent.click(screen.getByRole('button', { name: '接入并保存' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith({
      name: '新集群', kubeconfig, context: 'prod', test_id: 'valid-test-token',
    }));
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
  });

  it.each([null, undefined, '', '   '])('does not enable save when the test_id is %s', async (testId) => {
    vi.mocked(testKubernetesClusterDraft).mockResolvedValue({ ...passed, test_id: testId });
    const { onCreate } = show();
    enterConfig();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText('测试凭据缺失，请重新测试')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it.each(['API Server 地址', 'kubeconfig context', 'kubeconfig'])('invalidates a completed test after %s changes', async (label) => {
    show(); enterConfig(); await passTest();
    fireEvent.change(screen.getByLabelText(label), { target: { value: 'changed-config' } });
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
    expect(screen.queryByText('测试通过')).not.toBeInTheDocument();
  });

  it.each(['API Server 地址', 'kubeconfig context', 'kubeconfig'])('ignores a pending test response after %s changes', async (label) => {
    const pending = deferred<KubernetesClusterTestResult>();
    vi.mocked(testKubernetesClusterDraft).mockReturnValueOnce(pending.promise);
    show(); enterConfig();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(testKubernetesClusterDraft).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByLabelText(label), { target: { value: 'new-config' } });
    await act(async () => { pending.resolve(passed); });
    expect(screen.queryByText('测试通过')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
  });

  it('ignores a pending test after the secret form has been replaced', async () => {
    const pending = deferred<KubernetesClusterTestResult>();
    vi.mocked(testKubernetesClusterDraft).mockReturnValueOnce(pending.promise);
    const old = show(); enterConfig();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(testKubernetesClusterDraft).toHaveBeenCalledOnce());
    old.unmount(); show();
    await act(async () => { pending.resolve(passed); });
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
    expect(screen.queryByText('测试通过')).not.toBeInTheDocument();
  });

  it('blocks repeat tests and shows Chinese failures without upstream text', async () => {
    const pending = deferred<KubernetesClusterTestResult>();
    vi.mocked(testKubernetesClusterDraft).mockReturnValueOnce(pending.promise);
    show(); enterConfig();
    const testButton = screen.getByRole('button', { name: '测试连接' });
    fireEvent.click(testButton);
    expect(testButton).toBeDisabled();
    fireEvent.click(testButton);
    expect(testKubernetesClusterDraft).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve({
      status: 'failed', error_code: 'permission_denied', error_message: 'static-sensitive-value',
      steps: [{ name: 'resource_permissions', status: 'failed', code: 'forbidden' }],
    }); });
    expect(screen.getByText('集群读取权限不足，请检查只读授权')).toBeInTheDocument();
    expect(screen.queryByText('static-sensitive-value')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
  });

  it('clears the secret and proof while a save is pending and blocks duplicate saves', async () => {
    const { onCreate } = show();
    const pending = deferred<void>();
    onCreate.mockReturnValueOnce(pending.promise);
    fireEvent.change(screen.getByLabelText('集群名称'), { target: { value: '新集群' } });
    enterConfig(); await passTest();
    const saveButton = screen.getByRole('button', { name: '接入并保存' });
    fireEvent.click(saveButton);
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);
    expect(onCreate).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve(); });
    expect(screen.getByRole('button', { name: '接入并保存' })).toBeDisabled();
  });

  it('requires a fresh tested kubeconfig to rotate and sends the CAS revision and proof', async () => {
    const { onRotate, onCreate } = show(cluster);
    expect(screen.queryByLabelText('集群名称')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('关联业务服务 ID')).not.toBeInTheDocument();
    expect(screen.queryByText('采集周期')).not.toBeInTheDocument();
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
    expect(screen.getByRole('button', { name: '保存新凭据' })).toBeDisabled();
    enterConfig();
    fireEvent.change(screen.getByLabelText('轮换原因'), { target: { value: '凭据到期轮换' } });
    await passTest();
    fireEvent.click(screen.getByRole('button', { name: '保存新凭据' }));
    await waitFor(() => expect(onRotate).toHaveBeenCalledWith({
      kubeconfig, context: 'prod', endpoint: cluster.endpoint, expected_revision: 4,
      test_id: 'valid-test-token', reason: '凭据到期轮换',
    }));
    expect(onCreate).not.toHaveBeenCalled();
    expect(screen.getByLabelText('kubeconfig')).toHaveValue('');
  });

  it('shows a fixed request failure after a thrown response', async () => {
    vi.mocked(testKubernetesClusterDraft).mockRejectedValue(new ApiError('当前账号无权管理 Kubernetes 集群', 403));
    show(); enterConfig();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText('当前账号无权管理 Kubernetes 集群')).toBeInTheDocument();
  });
});

it('edits only metadata and permits clearing the linked service without rotation or collection controls', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <KubernetesMetadataForm cluster={cluster} busy={false} onCancel={vi.fn()} onSubmit={onSubmit} />
  </ConfigProvider>);
  const dialog = within(screen.getByRole('dialog', { name: '编辑 Kubernetes 集群' }));
  expect(dialog.queryByText(/API Server|kubeconfig|采集周期|关闭自动采集/)).not.toBeInTheDocument();
  fireEvent.change(dialog.getByLabelText('关联业务服务 ID'), { target: { value: '' } });
  fireEvent.click(dialog.getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ name: cluster.name, service_id: null, expected_revision: 4 }));
});
