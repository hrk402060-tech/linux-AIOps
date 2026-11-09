import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DockerGrantForm } from './DockerGrantForm';
import { getDiagnosticGrants, type DiagnosticGrant } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';

const asset = { id: 'asset-1', name: 'node-1', host: '192.0.2.10', lifecycle_status: 'enabled',
  credential_id: null, credential: null } as Asset;
const secondAsset = { ...asset, id: 'asset-2', name: 'node-2', host: '192.0.2.20' };
const ca = '-----BEGIN CERTIFICATE-----\nVEVTVC1PTkxZ\n-----END CERTIFICATE-----';
vi.mock('../../api/diagnostics', () => ({ getDiagnosticGrants: vi.fn() }));
const catalog = [
  { id: 'docker.containers', label: '容器状态', domain: 'docker', available: true, read_only: true, required_permission: 'docker:restricted_api', status: 'available' },
  { id: 'docker.resources', label: '容器资源', domain: 'docker', available: true, read_only: true, required_permission: 'docker:restricted_api', status: 'available' },
];
const grant: DiagnosticGrant = {
  kind: 'docker', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 443, username: 'proxy-reader',
  action_ids: ['docker.containers'], expires_at: dayjs().add(1, 'day').toISOString(), enabled: true,
  effective_status: 'active', revision: 3, key_fingerprint: '********', host_fingerprint: '',
  secret_configured: true, token_masked: '********', ca_fingerprint: 'SHA256:ca',
  docker_config: { endpoint: 'https://192.0.2.10:443', proxy_identity: 'proxy-reader' },
  operator: 'admin', updated_at: '2026-10-15T00:00:00Z',
};
function show(initialGrant: DiagnosticGrant | null = grant, suppliedAssets = [asset, secondAsset]) {
  const onSubmit = vi.fn();
  const component = (busy = false, canSubmit = true) => <ConfigProvider theme={{ token: { motion: false } }}><DockerGrantForm assets={suppliedAssets}
    catalog={catalog} initialGrant={initialGrant} busy={busy} canSubmit={canSubmit} onCancel={vi.fn()} onSubmit={onSubmit} /></ConfigProvider>;
  const rendered = render(component());
  return { ...rendered, component, onSubmit, dialog: within(screen.getByRole('dialog', { name: '配置 Docker 只读授权' })) };
}
function confirm(dialog: ReturnType<typeof within>) {
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '仅采集容器只读证据' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 Docker 代理只读范围' }));
}
function choose(dialog: ReturnType<typeof within>, label: string) {
  fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
  fireEvent.click(screen.getByText(label, { selector: '.ant-select-item-option-content' }));
}
beforeEach(() => { vi.mocked(getDiagnosticGrants).mockReset(); });

describe('DockerGrantForm', () => {
  it('shows masked proxy credentials and never asks for a container ID', () => {
    const { dialog } = show();
    expect(dialog.getByLabelText('代理 Token')).toHaveValue('');
    expect(dialog.getByText('留空保留 CA：SHA256:ca')).toBeInTheDocument();
    expect(dialog.queryByLabelText(/容器 ID/i)).not.toBeInTheDocument();
  });

  it('requires exact asset confirmation and submits the frozen Docker contract', async () => {
    const { dialog, onSubmit } = show();
    confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'wrong' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' node-1 ' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      kind: 'docker', asset_id: 'asset-1', expected_revision: 3, endpoint: 'https://192.0.2.10:443',
      proxy_identity: 'proxy-reader', action_ids: ['docker.containers'], proxy_confirmed: true,
    });
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('container_id');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('token');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ca_pem');
  });

  it('requires token and CA for a first grant', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog, onSubmit } = show(null);
    choose(dialog, 'node-1 · 192.0.2.10');
    await waitFor(() => expect(dialog.getByLabelText('代理身份')).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('代理身份'), { target: { value: 'proxy-reader' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '容器状态' }));
    const expiry = dialog.getByLabelText('授权有效期');
    fireEvent.change(expiry, { target: { value: dayjs().add(1, 'day').format('YYYY-MM-DD HH:mm:ss') } });
    fireEvent.keyDown(expiry, { key: 'Enter', code: 'Enter' });
    confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'token-value' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: ca } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ kind: 'docker', expected_revision: 0, token: 'token-value', ca_pem: ca });
  });

  it.each(['代理身份', 'Docker 代理 HTTPS 地址'])('requires a new token for %s changes while retaining the existing CA', async (field) => {
    const { dialog, onSubmit } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText(field), { target: { value: field === '代理身份' ? 'other-reader' : 'https://192.0.2.10:8443' } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'new-proxy-token' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ expected_revision: 3, token: 'new-proxy-token' });
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ca_pem');
  });

  it('requires a fresh token for CA rotation', async () => {
    const { dialog, onSubmit } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: ca } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'rotated-proxy-token' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ token: 'rotated-proxy-token', ca_pem: ca });
  });

  it.each([' token', 'token ', 'token value', 'token\tvalue', 'token\u007fvalue', '令牌', 't'.repeat(16385)])('rejects invalid raw tokens without silently trimming them: case %#', async (value) => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value } });
    expect(submit).toBeDisabled();
  });

  it('accepts the 16 KiB token and 64 character identity boundaries but rejects longer identities', async () => {
    const { dialog } = show(); confirm(dialog);
    expect(dialog.getByLabelText('代理身份')).toHaveAttribute('maxLength', '64');
    fireEvent.change(dialog.getByLabelText('代理身份'), { target: { value: 'a'.repeat(65) } });
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 't'.repeat(16384) } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('代理身份'), { target: { value: 'a'.repeat(64) } });
    await waitFor(() => expect(submit).toBeEnabled());
  });

  it.each(['http://192.0.2.10', 'https://example.test', 'https://192.0.2.20', 'https://192.0.2.10/', 'https://192.0.2.10:0', 'https://192.000.2.10'])('blocks invalid endpoints: %s', async (endpoint) => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('Docker 代理 HTTPS 地址'), { target: { value: endpoint } });
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'new-proxy-token' } });
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('compares equivalent IPv6 endpoint addresses', async () => {
    const ipv6Asset = { ...asset, host: '2001:db8:0:0:0:0:0:10' };
    const initial = { ...grant, host: ipv6Asset.host, docker_config: { ...grant.docker_config!, endpoint: 'https://[2001:db8::10]' } };
    const { dialog } = show(initial, [ipv6Asset]); confirm(dialog);
    await waitFor(() => expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeEnabled());
  });

  it('brackets a newly selected IPv6 target', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog } = show(null, [{ ...asset, host: '2001:db8::10' }]);
    choose(dialog, 'node-1 · 2001:db8::10');
    await waitFor(() => expect(dialog.getByLabelText('代理身份')).toBeEnabled());
    expect(dialog.getByLabelText('Docker 代理 HTTPS 地址')).toHaveValue('https://[2001:db8::10]');
  });

  it('bounds the CA chain to five certificates and validates its raw size', async () => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'rotated-proxy-token' } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    for (const count of [1, 2, 5]) {
      fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: Array<string>(count).fill(ca).join('\n') } });
      await waitFor(() => expect(submit).toBeEnabled());
    }
    for (const invalid of [Array<string>(6).fill(ca).join('\n'), `${ca}\nnot-a-certificate`, `${ca}${' '.repeat(32768)}`]) {
      fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: invalid } });
      expect(submit).toBeDisabled();
    }
  });

  it('clears sensitive draft fields and pending lookups when an asset changes', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'temporary-token' } });
    fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: ca } });
    choose(dialog, 'node-2 · 192.0.2.20');
    expect(dialog.getByLabelText('代理 Token')).toHaveValue('');
    expect(dialog.getByLabelText('代理 CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: '已人工确认 Docker 代理只读范围' })).not.toBeChecked();
    await waitFor(() => expect(dialog.getByLabelText('Docker 代理 HTTPS 地址')).toHaveValue('https://192.0.2.20'));
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('ignores a late asset response after the form is unmounted', async () => {
    let resolveOld: ((value: Awaited<ReturnType<typeof getDiagnosticGrants>>) => void) | undefined;
    vi.mocked(getDiagnosticGrants).mockImplementation(() => new Promise((resolve) => { resolveOld = resolve; }));
    const old = show(null);
    choose(old.dialog, 'node-1 · 192.0.2.10');
    old.unmount();
    const next = show(null);
    await act(async () => { resolveOld?.({ total: 1, items: [grant], catalog }); });
    expect(next.dialog.getByLabelText('代理身份')).toHaveValue('');
    expect(next.dialog.getByLabelText('代理 Token')).toHaveValue('');
    expect(next.onSubmit).not.toHaveBeenCalled();
  });

  it('refuses to turn a pre-existing grant of another kind into a Docker grant', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 1, items: [{ ...grant, kind: 'linux' }], catalog });
    const { dialog } = show(null);
    choose(dialog, 'node-1 · 192.0.2.10');
    expect(await screen.findByText('该资产已有其他只读授权，不允许转换授权类型')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('blocks submission while busy and clears credentials when submit permission is lost', async () => {
    const { dialog, rerender, component, onSubmit } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('代理 Token'), { target: { value: 'draft-token' } });
    fireEvent.change(dialog.getByLabelText('代理 CA PEM'), { target: { value: ca } });
    rerender(component(true));
    expect(dialog.getByRole('button', { name: /保存只读授权/ })).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: /保存只读授权/ }));
    expect(onSubmit).not.toHaveBeenCalled();
    rerender(component(false, false));
    await waitFor(() => expect(dialog.getByLabelText('代理 Token')).toHaveValue(''));
    expect(dialog.getByLabelText('代理 CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });
});
