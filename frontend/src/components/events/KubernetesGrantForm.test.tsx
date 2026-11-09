import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getDiagnosticGrants, type DiagnosticGrant } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { KubernetesGrantForm } from './KubernetesGrantForm';

vi.mock('../../api/diagnostics', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../api/diagnostics')>(), getDiagnosticGrants: vi.fn(),
}));
const asset = { id: 'asset-1', name: 'cluster-1', host: '192.0.2.10', lifecycle_status: 'enabled',
  credential_id: null, credential: null } as Asset;
const catalog = ['pods', 'workloads', 'events'].map((id) => ({
  id: `kubernetes.${id}`, label: `Kubernetes ${id}`, domain: 'kubernetes', available: true,
  read_only: true, required_permission: 'kubernetes:observe', status: 'available',
}));
const grant: DiagnosticGrant = {
  kind: 'kubernetes', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 6443,
  username: '', action_ids: ['kubernetes.pods'], expires_at: dayjs().add(1, 'day').toISOString(),
  enabled: true, effective_status: 'active', revision: 4, secret_configured: true,
  key_fingerprint: 'SHA256:token', host_fingerprint: '', ca_fingerprint: 'SHA256:ca',
  kubernetes_config: { endpoint: 'https://192.0.2.10:6443', service_account_namespace: 'observability',
    service_account_name: 'diagnostic-reader', namespaces: ['apps'] },
  operator: 'admin', updated_at: '2026-10-15T00:00:00Z',
};
const ca = '-----BEGIN CERTIFICATE-----\ndGVzdC1vbmx5LWNh\n-----END CERTIFICATE-----';
const encodeJwtPayload = (payload: Record<string, string | number>) => btoa(JSON.stringify(payload))
  .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
const token = (days = 2, name = 'diagnostic-reader') => `e30.${encodeJwtPayload({
  sub: `system:serviceaccount:observability:${name}`, exp: dayjs().add(days, 'day').unix(),
})}.test-only-signature`;
function show(initialGrant: DiagnosticGrant | null = grant, targetAsset = asset, additionalAssets: Asset[] = []) {
  const onSubmit = vi.fn();
  const result = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <KubernetesGrantForm assets={[targetAsset, { ...asset, id: 'asset-2', name: 'cluster-2', host: '192.0.2.20' }, ...additionalAssets]}
      catalog={catalog} initialGrant={initialGrant} busy={false} canSubmit onCancel={vi.fn()} onSubmit={onSubmit} />
  </ConfigProvider>);
  return { ...result, onSubmit, dialog: within(screen.getByRole('dialog', { name: '配置 Kubernetes 只读授权' })) };
}
function confirm(dialog: ReturnType<typeof within>) {
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅采集指定命名空间证据' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' cluster-1 ' } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已人工核对专用 ServiceAccount 只读 RBAC' }));
}
afterEach(() => { vi.clearAllMocks(); });

describe('KubernetesGrantForm', () => {
  it('excludes assets with SSH credential IDs or credential records from cluster mapping choices', async () => {
    const { dialog } = show(null, asset, [
      { ...asset, id: 'ssh-id', name: 'SSH ID asset', credential_id: 'credential-1' },
      { ...asset, id: 'ssh-record', name: 'SSH record asset', credential: {
        id: 'credential-2', name: 'SSH login', username: 'root', credential_type: 'password',
      } },
      { ...asset, id: 'disabled', name: 'disabled asset', lifecycle_status: 'disabled' },
    ]);
    expect(dialog.getByText('集群映射资产')).toBeInTheDocument();
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
    expect(screen.getByText('cluster-1 · 192.0.2.10', { selector: '.ant-select-item-option-content' })).toBeInTheDocument();
    expect(screen.getByText('cluster-2 · 192.0.2.20', { selector: '.ant-select-item-option-content' })).toBeInTheDocument();
    expect(screen.queryByText('SSH ID asset · 192.0.2.10')).not.toBeInTheDocument();
    expect(screen.queryByText('SSH record asset · 192.0.2.10')).not.toBeInTheDocument();
    expect(screen.queryByText('disabled asset · 192.0.2.10')).not.toBeInTheDocument();
  });

  it('retains masked credentials only with unchanged identity and requires exact human confirmation', async () => {
    const { dialog, onSubmit } = show();
    expect(dialog.getByLabelText('ServiceAccount Token')).toHaveValue('');
    expect(dialog.getByLabelText('集群 CA PEM')).toHaveValue('');
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    confirm(dialog);
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'Cluster-1' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' cluster-1 ' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'kubernetes', expected_revision: 4, endpoint: 'https://192.0.2.10:6443',
      namespaces: ['apps'], action_ids: ['kubernetes.pods'], confirmation: 'cluster-1', rbac_confirmed: true,
    }));
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('token');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ca_pem');
  });

  it('requires fresh token on endpoint, ServiceAccount identity or CA change', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('API Server HTTPS 地址'), { target: { value: 'https://192.0.2.10:7443' } });
    expect(submit).toBeDisabled();
    const firstToken = token();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: firstToken } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('ServiceAccount 名称'), { target: { value: 'new-reader' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token(2, 'new-reader') } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: '' } });
    fireEvent.change(dialog.getByLabelText('集群 CA PEM'), { target: { value: ca } });
    expect(submit).toBeDisabled();
  });

  it.each(['http://192.0.2.10:6443', 'https://192.0.2.20:6443', 'https://localhost:6443',
    'https://127.0.0.1:6443', 'https://169.254.1.1:6443', 'https://192.0.2.10:6443/api',
    'https://user@192.0.2.10:6443', 'https://192.0.2.10:6443?watch=1',
    'https://192.0.2.10:6443/'])('rejects unsafe or mismatching endpoint %s', async (endpoint) => {
    const { dialog, onSubmit } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    fireEvent.change(dialog.getByLabelText('API Server HTTPS 地址'), { target: { value: endpoint } });
    expect(submit).toBeDisabled(); expect(onSubmit).not.toHaveBeenCalled();
  });

  it('allows omitted port as HTTPS default while preserving the create default of 6443', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    fireEvent.change(dialog.getByLabelText('API Server HTTPS 地址'), { target: { value: 'https://192.0.2.10' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('API Server HTTPS 地址'), { target: { value: 'https://192.0.2.10/' } });
    expect(submit).toBeDisabled();
  });

  it('rejects IPv4-mapped IPv6 even when it matches the bound asset IP', async () => {
    const mappedHost = '::ffff:c000:20a';
    const mappedGrant = { ...grant, host: mappedHost, kubernetes_config: {
      ...grant.kubernetes_config!, endpoint: `https://[${mappedHost}]:6443`,
    } };
    const { dialog } = show(mappedGrant, { ...asset, host: mappedHost }); confirm(dialog);
    expect(dialog.getByLabelText('API Server HTTPS 地址')).toHaveValue(`https://[${mappedHost}]:6443`);
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('accepts one to five PEM certificates and rejects oversized or malformed CA bundles', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    const input = dialog.getByLabelText('集群 CA PEM');
    expect(input).toHaveAttribute('maxLength', '32768');
    for (const count of [1, 2, 5]) {
      fireEvent.change(input, { target: { value: Array<string>(count).fill(ca).join('\n') } });
      await waitFor(() => expect(submit).toBeEnabled());
    }
    for (const invalid of [Array<string>(6).fill(ca).join('\n'), `${ca}\nnot-a-certificate`, `${ca}${' '.repeat(32768)}`]) {
      fireEvent.change(input, { target: { value: invalid } });
      expect(submit).toBeDisabled();
    }
  });

  it('rejects fractional JWT expiry without claiming a signature verification', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    const fractional = `e30.${encodeJwtPayload({ sub: 'system:serviceaccount:observability:diagnostic-reader',
      exp: dayjs().add(2, 'day').unix() + 0.5 })}.test-only-signature`;
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: fractional } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    await waitFor(() => expect(submit).toBeEnabled());
    expect(dialog.getByText('本地仅检查 JWT 的 SA 身份与有效期，不验证签名；真实身份由 Kubernetes API 认证。')).toBeInTheDocument();
  });

  it('limits namespace scope to one to three explicit names and token expiry to the grant', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    for (const namespaces of ['', '*', 'apps,apps', 'one,two,three,four', 'apps,kube-*']) {
      fireEvent.change(dialog.getByLabelText('允许的命名空间'), { target: { value: namespaces } });
      expect(submit).toBeDisabled();
    }
    fireEvent.change(dialog.getByLabelText('允许的命名空间'), { target: { value: 'apps, metrics' } });
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token(0) } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token(2, 'wrong-reader') } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(dialog.getByRole('checkbox', { name: 'Kubernetes pods' }));
    expect(submit).toBeDisabled();
  });

  it('requires token and CA on first grant and sends only the Kubernetes contract', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog, onSubmit } = show(null);
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
    fireEvent.click(screen.getByText('cluster-1 · 192.0.2.10', { selector: '.ant-select-item-option-content' }));
    await waitFor(() => expect(dialog.getByLabelText('API Server HTTPS 地址')).toHaveValue('https://192.0.2.10:6443'));
    fireEvent.change(dialog.getByLabelText('ServiceAccount 所在命名空间'), { target: { value: 'observability' } });
    fireEvent.change(dialog.getByLabelText('ServiceAccount 名称'), { target: { value: 'diagnostic-reader' } });
    fireEvent.change(dialog.getByLabelText('允许的命名空间'), { target: { value: 'apps' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: 'Kubernetes pods' }));
    const expiry = dialog.getByLabelText('授权有效期');
    fireEvent.change(expiry, { target: { value: dayjs().add(1, 'day').format('YYYY-MM-DD HH:mm:ss') } });
    fireEvent.keyDown(expiry, { key: 'Enter', code: 'Enter' }); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    const firstToken = token();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: firstToken } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('集群 CA PEM'), { target: { value: ca } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ kind: 'kubernetes', expected_revision: 0, token: firstToken, ca_pem: ca });
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('private_key');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('gateway_confirmed');
  });

  it('clears secrets on asset changes before loading the newly selected grant', async () => {
    let resolveOld: ((value: Awaited<ReturnType<typeof getDiagnosticGrants>>) => void) | undefined;
    vi.mocked(getDiagnosticGrants).mockImplementation((_offset, id) => id === 'asset-1'
      ? new Promise((resolve) => { resolveOld = resolve; }) : Promise.resolve({ total: 0, items: [], catalog }));
    const { dialog } = show();
    fireEvent.change(dialog.getByLabelText('ServiceAccount Token'), { target: { value: token() } });
    fireEvent.change(dialog.getByLabelText('集群 CA PEM'), { target: { value: ca } });
    const choose = (name: string) => {
      fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
      fireEvent.click(screen.getByText(name, { selector: '.ant-select-item-option-content' }));
    };
    choose('cluster-2 · 192.0.2.20');
    await waitFor(() => expect(dialog.getByLabelText('ServiceAccount Token')).toBeEnabled());
    expect(dialog.getByLabelText('ServiceAccount Token')).toHaveValue('');
    expect(dialog.getByLabelText('集群 CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    choose('cluster-1 · 192.0.2.10');
    await act(async () => { resolveOld?.({ total: 1, items: [grant], catalog }); });
    await waitFor(() => expect(dialog.getByLabelText('ServiceAccount 名称')).toHaveValue('diagnostic-reader'));
  });

  it('refuses changing an existing Linux grant into Kubernetes', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 1, items: [{ ...grant, kind: 'linux' }], catalog });
    const { dialog } = show(null);
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
    fireEvent.click(screen.getByText('cluster-1 · 192.0.2.10', { selector: '.ant-select-item-option-content' }));
    expect(await screen.findByText('该资产已有 Linux 授权，不允许转换授权类型')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('does not apply an old asset lookup after the secret-bearing form unmounts', async () => {
    let resolveOld: ((value: Awaited<ReturnType<typeof getDiagnosticGrants>>) => void) | undefined;
    vi.mocked(getDiagnosticGrants).mockImplementation(() => new Promise((resolve) => { resolveOld = resolve; }));
    const old = show(null);
    fireEvent.mouseDown(old.dialog.getByRole('combobox', { name: '授权服务器' }));
    fireEvent.click(screen.getByText('cluster-1 · 192.0.2.10', { selector: '.ant-select-item-option-content' }));
    old.unmount();
    const next = show(null);
    await act(async () => { resolveOld?.({ total: 1, items: [grant], catalog }); });
    expect(next.dialog.getByLabelText('ServiceAccount 名称')).toHaveValue('');
    expect(next.dialog.getByLabelText('ServiceAccount Token')).toHaveValue('');
    expect(next.dialog.getByLabelText('集群 CA PEM')).toHaveValue('');
    expect(next.onSubmit).not.toHaveBeenCalled();
  });
});
