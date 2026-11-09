import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import dayjs from 'dayjs';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DiagnosticGrantsTab } from './DiagnosticGrantsTab';

const asset = {
  id: 'asset-1', name: 'node-1', host: '192.0.2.10', ssh_port: 22,
  os_family: 'openeuler', lifecycle_status: 'enabled', credential: { username: 'root' },
};
const grant = {
  asset_id: 'asset-1', asset_name: 'node-1', host: '192.0.2.10', port: 22,
  username: 'diagnostic', action_ids: ['linux.uptime'], expires_at: '2099-01-01T00:00:00Z',
  enabled: true, effective_status: 'active', revision: 3, key_fingerprint: 'SHA256:public-only',
  host_fingerprint: 'SHA256:host-only', secret_configured: true,
  operator: 'admin', updated_at: '2026-10-07T01:00:00Z',
};
const catalog = [
  { id: 'linux.uptime', label: '负载与运行时间', domain: 'linux', available: true, read_only: true, required_permission: 'ssh:observe', status: 'available' },
  { id: 'k8s.pods', label: 'K8s Pod', domain: 'k8s', available: false, read_only: true, required_permission: 'pending', status: 'planned' },
];
const kubernetesGrant = {
  ...grant, kind: 'kubernetes', username: '', port: 6443, action_ids: ['kubernetes.pods'],
  expires_at: dayjs().add(1, 'day').toISOString(), ca_fingerprint: 'SHA256:k8s-ca',
  kubernetes_config: { endpoint: 'https://192.0.2.10:6443', service_account_namespace: 'observability', service_account_name: 'diagnostic-reader', namespaces: ['apps', 'metrics'] },
};
const kubernetesCatalog = ['pods', 'workloads', 'events'].map((id) => ({
  id: `kubernetes.${id}`, label: `Kubernetes ${id}`, domain: 'kubernetes', available: true, read_only: true, required_permission: 'kubernetes:observe', status: 'available',
}));
const dockerGrant = {
  ...grant, kind: 'docker', username: 'proxy-reader', port: 443, action_ids: ['docker.containers'],
  expires_at: dayjs().add(1, 'day').toISOString(), ca_fingerprint: 'SHA256:docker-ca',
  docker_config: { endpoint: 'https://192.0.2.10:443', proxy_identity: 'proxy-reader' },
};
const dockerCatalog = ['containers', 'resources'].map((id) => ({
  id: `docker.${id}`, label: id === 'containers' ? '容器状态' : '容器资源', domain: 'docker',
  available: true, read_only: true, required_permission: 'docker:restricted_api', status: 'available',
}));
const response = (body: unknown, status = 200) => Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
function stubApi(total = 1, kubernetes = false) {
  const apiAsset = kubernetes ? { ...asset, credential_id: null, credential: null } : asset;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    if (url.pathname === '/api/v1/assets') return response([apiAsset, { ...apiAsset, id: 'asset-2', name: 'node-2', host: '192.0.2.20' }, { ...apiAsset, id: 'disabled-1', name: '停用资产', lifecycle_status: 'disabled' }]);
    if (url.pathname === '/api/v1/diagnostic-grants' && method === 'GET') return response({ total, items: [grant], catalog });
    if (url.pathname === '/api/v1/diagnostic-grants' && method === 'POST') return response({ ...grant, revision: 4 });
    if (url.pathname === '/api/v1/diagnostic-grants/asset-1/test' && method === 'POST') return response({
      asset_id: 'asset-1', kind: 'linux', status: 'completed', summary: '只读授权验证通过。',
      read_only: true, grant_revision: 3, evidence_keys: ['linux.uptime'],
    });
    if (url.pathname === '/api/v1/diagnostic-grants/asset-1/revoke') return response({ ...grant, enabled: false, effective_status: 'disabled', revision: 4 });
    if (url.pathname === '/api/v1/diagnostic-grants/asset-1/audit') return response({ items: [{ id: 'audit-1', asset_id: 'asset-1', action: 'configured', operator: 'admin', reason: '只读授权', revision: 3, created_at: '2026-10-07T01:00:00Z' }] });
    throw new Error(`Unexpected diagnostic API: ${method} ${url.pathname}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
function show(role: 'admin' | 'operator' | 'auditor' = 'admin') {
  return render(<ConfigProvider theme={{ token: { motion: false } }}><DiagnosticGrantsTab currentUser={{ role }} /></ConfigProvider>);
}
function stubDocker() {
  const mock = stubApi(1, true); const normal = mock.getMockImplementation()!;
  mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants?')
    ? response({ total: 1, items: [dockerGrant], catalog: [...catalog, ...kubernetesCatalog, ...dockerCatalog] })
    : init?.method === 'POST' ? response({ ...dockerGrant, revision: 4 }) : normal(input, init));
  return mock;
}
async function editDocker() {
  fireEvent.click(await screen.findByRole('button', { name: '配置 node-1 只读授权' }));
  const dialog = within(screen.getByRole('dialog', { name: '配置 Docker 只读授权' }));
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '只读代理范围确认' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 Docker 代理只读范围' }));
  return dialog;
}
async function edit() {
  fireEvent.click(await screen.findByRole('button', { name: '配置 node-1 只读授权' }));
  return within(await screen.findByRole('dialog', { name: '配置只读诊断授权' }));
}
async function completeForm(dialog: ReturnType<typeof within>, confirmation = ' node-1 ') {
  const expiry = dialog.getByLabelText('授权有效期');
  fireEvent.change(expiry, { target: { value: dayjs().add(1, 'day').format('YYYY-MM-DD HH:mm:ss') } });
  fireEvent.keyDown(expiry, { key: 'Enter', code: 'Enter' });
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅用于告警只读采集' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: confirmation } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' }));
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('DiagnosticGrantsTab', () => {
  it('shows Docker proxy identity and masked credentials while locking the edited grant kind', async () => {
    stubDocker(); show();
    expect(await screen.findByText('https://192.0.2.10:443')).toBeInTheDocument();
    expect(screen.getByText('proxy-reader')).toBeInTheDocument();
    expect(screen.getByText('Token：********')).toBeInTheDocument();
    expect(screen.getByText('SHA256:docker-ca')).toBeInTheDocument();
    const dialog = await editDocker();
    expect(dialog.getByLabelText('代理 Token')).toHaveValue('');
    expect(dialog.queryByLabelText('诊断私钥')).not.toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Linux' })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Kubernetes' })).not.toBeInTheDocument();
  });

  it('submits a Docker grant once on double click and destroys the draft on success', async () => {
    const mock = stubDocker(); show();
    const dialog = await editDocker();
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit); fireEvent.click(submit);
    await waitFor(() => expect(mock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const payload = JSON.parse(String(mock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body));
    expect(payload).toMatchObject({ kind: 'docker', expected_revision: 3, confirmation: 'node-1', proxy_confirmed: true });
    expect(payload).not.toHaveProperty('token'); expect(payload).not.toHaveProperty('ca_pem');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it.each([403, 409])('masks Docker HTTP %s failures and closes the secret-bearing form', async (status) => {
    const mock = stubDocker(); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => init?.method === 'POST'
      ? response({ detail: 'sensitive-proxy-token-and-ca' }, status) : normal(input, init));
    show(); const dialog = await editDocker();
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'sensitive-proxy-token' } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled()); fireEvent.click(submit);
    expect(await screen.findByText('Docker 代理授权保存失败，请检查地址、证书、身份与凭据后重试')).toBeInTheDocument();
    expect(screen.queryByText('sensitive-proxy-token-and-ca')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByDisplayValue('sensitive-proxy-token')).not.toBeInTheDocument();
  });

  it('destroys Docker sensitive drafts when changing authorization kind and closing the form', async () => {
    stubDocker(); show();
    const create = await screen.findByRole('button', { name: '新增只读授权' });
    await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
    fireEvent.click(screen.getByRole('radio', { name: 'Docker' }));
    fireEvent.change(screen.getByLabelText('代理 Token'), { target: { value: 'temporary-docker-token' } });
    fireEvent.change(screen.getByLabelText('代理 CA PEM'), { target: { value: 'temporary-ca' } });
    fireEvent.change(screen.getByLabelText('资产名称确认'), { target: { value: 'node-1' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Kubernetes' }));
    expect(screen.queryByLabelText('代理 Token')).not.toBeInTheDocument();
    expect(screen.getByLabelText('ServiceAccount Token')).toHaveValue('');
    fireEvent.click(screen.getByRole('radio', { name: 'Docker' }));
    expect(screen.getByLabelText('代理 Token')).toHaveValue('');
    expect(screen.getByLabelText('代理 CA PEM')).toHaveValue('');
    expect(screen.getByLabelText('资产名称确认')).toHaveValue('');
    fireEvent.click(within(screen.getByRole('dialog', { name: '配置 Docker 只读授权' })).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('closes Docker drafts immediately after an administrator role downgrade', async () => {
    const mock = stubDocker(); const view = show();
    const dialog = await editDocker();
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'draft-secret-token' } });
    fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: 'draft-ca' } });
    view.rerender(<ConfigProvider theme={{ token: { motion: false } }}><DiagnosticGrantsTab currentUser={{ role: 'operator' }} /></ConfigProvider>);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByDisplayValue('draft-secret-token')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it.each(['operator', 'auditor'] as const)('keeps Docker grants read-only for %s while preserving audit access', async (role) => {
    const mock = stubDocker(); show(role);
    await screen.findByText('proxy-reader');
    expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置 node-1 只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '撤销 node-1 只读授权' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 node-1 授权记录' }));
    expect(await screen.findByText('只读授权')).toBeInTheDocument();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('shows Kubernetes identity, scope and masked token with CA fingerprint and locks edit kind', async () => {
    const mock = stubApi(1, true); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants?')
      ? response({ total: 1, items: [kubernetesGrant], catalog: [...catalog, ...kubernetesCatalog] }) : normal(input, init));
    show();
    expect(await screen.findByText('https://192.0.2.10:6443')).toBeInTheDocument();
    expect(screen.getByText('observability / diagnostic-reader')).toBeInTheDocument();
    expect(screen.getByText('命名空间：apps、metrics')).toBeInTheDocument();
    expect(screen.getByText('Token：********')).toBeInTheDocument(); expect(screen.getByText('SHA256:k8s-ca')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '配置 node-1 只读授权' }));
    const dialog = within(await screen.findByRole('dialog', { name: '配置 Kubernetes 只读授权' }));
    expect(dialog.getByLabelText('ServiceAccount Token')).toHaveValue(''); expect(dialog.queryByLabelText('诊断私钥')).not.toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Linux' })).not.toBeInTheDocument();
  });
  it('clears secret and confirmation on kind switch and closing then reopening a create form', async () => {
    stubApi(); show();
    const create = await screen.findByRole('button', { name: '新增只读授权' });
    await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
    fireEvent.change(screen.getByLabelText('诊断私钥'), { target: { value: 'test-only-private-material' } });
    fireEvent.change(screen.getByLabelText('资产名称确认'), { target: { value: 'node-1' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Kubernetes' }));
    expect(screen.queryByLabelText('诊断私钥')).not.toBeInTheDocument();
    expect(screen.getByLabelText('资产名称确认')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('ServiceAccount Token'), { target: { value: 'test-only-token' } });
    fireEvent.change(screen.getByLabelText('集群 CA PEM'), { target: { value: 'test-only-ca' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Linux' }));
    expect(screen.getByLabelText('诊断私钥')).toHaveValue('');
    fireEvent.click(screen.getByRole('radio', { name: 'Kubernetes' }));
    expect(screen.getByLabelText('ServiceAccount Token')).toHaveValue(''); expect(screen.getByLabelText('集群 CA PEM')).toHaveValue('');
    fireEvent.click(within(screen.getByRole('dialog', { name: '配置 Kubernetes 只读授权' })).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(create);
    expect(screen.getByLabelText('诊断私钥')).toHaveValue(''); expect(screen.getByLabelText('资产名称确认')).toHaveValue('');
  });
  it('submits Kubernetes authorization only once and clears the secret-bearing form on success', async () => {
    const mock = stubApi(1, true); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants?')
      ? response({ total: 1, items: [kubernetesGrant], catalog: [...catalog, ...kubernetesCatalog] }) : normal(input, init));
    show(); fireEvent.click(await screen.findByRole('button', { name: '配置 node-1 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 Kubernetes 只读授权' }));
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '只读范围确认' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工核对专用 ServiceAccount 只读 RBAC' }));
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled()); fireEvent.click(submit); fireEvent.click(submit);
    await waitFor(() => expect(mock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const payload = JSON.parse(String(mock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body));
    expect(payload).toMatchObject({ kind: 'kubernetes', expected_revision: 3, rbac_confirmed: true, namespaces: ['apps', 'metrics'] });
    expect(payload).not.toHaveProperty('token'); expect(payload).not.toHaveProperty('ca_pem');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it.each([403, 409])('never displays Kubernetes secrets in HTTP %s errors and destroys the form', async (status) => {
    const mock = stubApi(1, true); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => init?.method === 'POST'
      ? response({ detail: 'test-only-sensitive-token-and-ca' }, status)
      : String(input).startsWith('/api/v1/diagnostic-grants?')
        ? response({ total: 1, items: [kubernetesGrant], catalog: [...catalog, ...kubernetesCatalog] }) : normal(input, init));
    show(); fireEvent.click(await screen.findByRole('button', { name: '配置 node-1 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 Kubernetes 只读授权' }));
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅作只读采集' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'node-1' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工核对专用 ServiceAccount 只读 RBAC' }));
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled()); fireEvent.click(submit);
    expect(await screen.findByText('Kubernetes 授权保存失败，请检查权限、版本、范围与凭据后重试')).toBeInTheDocument();
    expect(screen.queryByText('test-only-sensitive-token-and-ca')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it.each(['operator', 'auditor'] as const)('keeps Kubernetes controls read-only for %s', async (role) => {
    const mock = stubApi(1, true); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants?')
      ? response({ total: 1, items: [kubernetesGrant], catalog: kubernetesCatalog }) : normal(input, init));
    show(role);
    await screen.findByText('observability / diagnostic-reader');
    expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置 node-1 只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '撤销 node-1 只读授权' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 node-1 授权记录' }));
    expect(await screen.findByText('只读授权')).toBeInTheDocument();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });
  it('shows bounded grants and fingerprints without returning or prefilling private keys', async () => {
    stubApi(); show();
    expect(await screen.findByText('diagnostic')).toBeInTheDocument();
    expect(screen.getByText('已生效')).toBeInTheDocument();
    expect(screen.getByText('SHA256:host-only')).toBeInTheDocument();
    const dialog = await edit();
    expect(dialog.getByLabelText('诊断私钥')).toHaveValue('');
    expect(dialog.getByText('需输入：node-1')).toBeInTheDocument();
    expect(dialog.getByText('留空保留已加密私钥')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /执行命令/ })).not.toBeInTheDocument();
  });

  it('requires confirmation before testing and sends only metadata to the test endpoint', async () => {
    const mock = stubApi(); show();
    fireEvent.click(await screen.findByRole('button', { name: '验证 node-1 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '验证只读诊断授权' }));
    const test = dialog.getByRole('button', { name: '开始验证' });
    expect(test).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '上线前验证' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
    await waitFor(() => expect(test).toBeEnabled());
    fireEvent.click(test);
    await waitFor(() => expect(mock).toHaveBeenCalledWith('/api/v1/diagnostic-grants/asset-1/test', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ expected_revision: 3, confirmation: 'node-1', reason: '上线前验证' }),
    })));
    expect(await screen.findByText(/授权验证通过/)).toBeInTheDocument();
  });

  it.each(['operator', 'auditor'] as const)('keeps grants view-only for %s while audit remains readable', async (role) => {
    const mock = stubApi(); show(role);
    await screen.findByText('diagnostic');
    expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置 node-1 只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '撤销 node-1 只读授权' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 node-1 授权记录' }));
    expect(await screen.findByText('只读授权')).toBeInTheDocument();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('requires exact confirmation, reason and gateway attestation then omits a retained key', async () => {
    const mock = stubApi(); show();
    const dialog = await edit();
    await completeForm(dialog, 'wrong');
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit); fireEvent.click(submit);
    await waitFor(() => expect(mock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const body = mock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body;
    expect(JSON.parse(String(body))).toMatchObject({ asset_id: 'asset-1', expected_revision: 3, confirmation: 'node-1', gateway_confirmed: true, reason: '仅用于告警只读采集' });
    expect(JSON.parse(String(body))).not.toHaveProperty('private_key');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mock.mock.calls.filter(([url, init]) => String(url).startsWith('/api/v1/diagnostic-grants?') && !init?.method).length).toBe(2);
  });

  it.each([403, 409])('refreshes authoritative state after HTTP %s and clears the secret-bearing dialog', async (status) => {
    const mock = stubApi(); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => init?.method === 'POST'
      ? response({ detail: '授权状态或权限已变更' }, status) : normal(input, init));
    show(); const dialog = await edit();
    await completeForm(dialog);
    fireEvent.change(dialog.getByLabelText('诊断私钥'), { target: { value: 'test-only-private-material'.repeat(3) } });
    fireEvent.click(dialog.getByRole('button', { name: '保存只读授权' }));
    expect(await screen.findByText('授权状态或权限已变更')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByDisplayValue('test-only-private-material'.repeat(3))).not.toBeInTheDocument();
    expect(mock.mock.calls.filter(([url, init]) => String(url).startsWith('/api/v1/diagnostic-grants?') && !init?.method).length).toBe(2);
  });

  it('sends current revision and confirmation only after a human confirms revocation', async () => {
    const mock = stubApi(); show();
    fireEvent.click(await screen.findByRole('button', { name: '撤销 node-1 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '撤销只读诊断授权' }));
    const revoke = dialog.getByRole('button', { name: '确认撤销' });
    expect(revoke).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('撤销原因'), { target: { value: '关闭临时授权' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
    fireEvent.click(revoke);
    await waitFor(() => expect(mock).toHaveBeenCalledWith('/api/v1/diagnostic-grants/asset-1/revoke', expect.objectContaining({ method: 'POST', body: JSON.stringify({ expected_revision: 3, confirmation: 'node-1', reason: '关闭临时授权' }) })));
  });

  it('uses server pagination rather than truncating the grant list', async () => {
    const mock = stubApi(51); show('auditor');
    await screen.findByText('diagnostic');
    fireEvent.click(screen.getByTitle('Next Page').querySelector('button')!);
    await waitFor(() => expect(mock).toHaveBeenCalledWith('/api/v1/diagnostic-grants?offset=50&limit=50', expect.anything()));
  });

  it('resets private material when a different enabled asset is selected and disables future connectors', async () => {
    const mock = stubApi(); show();
    const create = await screen.findByRole('button', { name: '新增只读授权' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const dialog = within(screen.getByRole('dialog', { name: '配置只读诊断授权' }));
    const chooseAsset = (name: string) => {
      fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
      fireEvent.click(screen.getByText(name, { selector: '.ant-select-item-option-content' }));
    };
    chooseAsset('node-1 · 192.0.2.10');
    await waitFor(() => expect(dialog.getByLabelText('专用只读账号')).toHaveValue('diagnostic'));
    await waitFor(() => expect(dialog.getByLabelText('诊断私钥')).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('诊断私钥'), { target: { value: 'temporary-private-material' } });
    expect(dialog.getByLabelText('诊断私钥')).toHaveValue('temporary-private-material');
    chooseAsset('node-2 · 192.0.2.20');
    await waitFor(() => expect(mock).toHaveBeenCalledWith('/api/v1/diagnostic-grants?offset=0&limit=50&asset_id=asset-2', expect.anything()));
    expect(dialog.getByLabelText('诊断私钥')).toHaveValue('');
    expect(dialog.getByLabelText('专用只读账号')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: 'K8s Pod（暂未开放）' })).toBeDisabled();
    expect(screen.queryByText('停用资产', { selector: '.ant-select-item-option-content' })).not.toBeInTheDocument();
  });

  it('ignores a stale request response when changing the page', async () => {
    const mock = stubApi(51); const normal = mock.getMockImplementation()!;
    let resolveOld: ((value: Awaited<ReturnType<typeof response>>) => void) | undefined;
    let stale = false;
    mock.mockImplementation((input, init) => stale && String(input).includes('offset=0')
      ? new Promise((resolve) => { resolveOld = resolve; }) : normal(input, init));
    show('auditor'); await screen.findByText('diagnostic');
    stale = true;
    fireEvent.click(screen.getByRole('button', { name: '刷新只读授权' }));
    fireEvent.click(screen.getByTitle('Next Page').querySelector('button')!);
    await waitFor(() => expect(mock.mock.calls.some(([url]) => String(url).includes('offset=50'))).toBe(true));
    await act(async () => { resolveOld?.({ ok: true, status: 200, json: async () => ({ total: 51, items: [{ ...grant, username: 'stale-identity' }], catalog }) }); });
    await waitFor(() => expect(screen.queryByText('stale-identity')).not.toBeInTheDocument());
  });

  it('rejects root, missing gateway and short keys after proving a valid form can be saved', async () => {
    const mock = stubApi(); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants') && !init?.method
      ? response({ total: 1, items: [{ ...grant, expires_at: dayjs().add(1, 'day').toISOString() }], catalog }) : normal(input, init));
    show(); const dialog = await edit();
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    await waitFor(() => expect(dialog.getByLabelText('专用只读账号')).toHaveValue('diagnostic'));
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅用于告警只读采集' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'node-1' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' }));
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('专用只读账号'), { target: { value: 'root' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('专用只读账号'), { target: { value: 'diagnostic' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' }));
    expect(submit).toBeDisabled();
    fireEvent.click(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' }));
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('诊断私钥'), { target: { value: 'too-short' } });
    expect(submit).toBeDisabled();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('disables saving an explicit expiry beyond 90 days with all other authorization fields valid', async () => {
    const overBudget = dayjs().add(91, 'day');
    const mock = stubApi(); const normal = mock.getMockImplementation()!;
    mock.mockImplementation((input, init) => String(input).startsWith('/api/v1/diagnostic-grants') && !init?.method
      ? response({ total: 1, items: [{ ...grant, expires_at: overBudget.toISOString() }], catalog }) : normal(input, init));
    show(); const dialog = await edit();
    await waitFor(() => expect(dialog.getByLabelText('专用只读账号')).toHaveValue('diagnostic'));
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅用于告警只读采集' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'node-1' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' }));
    expect(dialog.getByLabelText('授权有效期')).toHaveValue(overBudget.format('YYYY-MM-DD HH:mm:ss'));
    expect(dialog.getByLabelText('专用只读账号')).toHaveValue('diagnostic');
    expect(dialog.getByLabelText('诊断私钥')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: '负载与运行时间' })).toBeChecked();
    expect(dialog.getByRole('checkbox', { name: '已在目标服务器人工安装限制网关' })).toBeChecked();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
    expect(mock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });
});
