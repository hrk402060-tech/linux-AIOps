import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getDiagnosticGrants, type DiagnosticGrant } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { MySQLGrantForm } from './MySQLGrantForm';

vi.mock('../../api/diagnostics', () => ({ getDiagnosticGrants: vi.fn() }));
const asset = { id: 'asset-1', name: 'db-1', host: '192.0.2.10', ssh_port: 22, lifecycle_status: 'enabled' } as Asset;
const secondAsset = { ...asset, id: 'asset-2', name: 'db-2', host: '192.0.2.20' };
const ca = '-----BEGIN CERTIFICATE-----\nVEVTVC1PTkxZ\n-----END CERTIFICATE-----';
const catalog = [
  { id: 'mysql.status', label: 'MySQL 状态', domain: 'mysql', available: true, read_only: true,
    required_permission: 'mysql:observe', status: 'available' },
  { id: 'mysql.replication', label: 'MySQL 复制状态', domain: 'mysql', available: true, read_only: true,
    required_permission: 'mysql:observe', status: 'available' },
];
const grant: DiagnosticGrant = {
  kind: 'mysql', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 3306, username: 'ops_reader',
  action_ids: ['mysql.status'], expires_at: dayjs().add(1, 'day').toISOString(), enabled: true,
  effective_status: 'active', revision: 3, key_fingerprint: '', host_fingerprint: '',
  secret_configured: true, ca_fingerprint: 'SHA256:ca',
  mysql_config: { host: asset.host, port: 3306, username: 'ops_reader', database: '' },
  operator: 'admin', updated_at: '2026-10-22T00:00:00Z',
};
function show(initialGrant: DiagnosticGrant | null = grant, suppliedAssets = [asset, secondAsset], suppliedCatalog = catalog) {
  const onSubmit = vi.fn();
  const component = (busy = false, canSubmit = true) => <ConfigProvider theme={{ token: { motion: false } }}>
    <MySQLGrantForm assets={suppliedAssets} catalog={suppliedCatalog} initialGrant={initialGrant}
      busy={busy} canSubmit={canSubmit} onCancel={vi.fn()} onSubmit={onSubmit} />
  </ConfigProvider>;
  const result = render(component());
  return { ...result, component, onSubmit, dialog: within(screen.getByRole('dialog', { name: '配置 MySQL 只读授权' })) };
}
function confirm(dialog: ReturnType<typeof within>) {
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: ' 采集数据库只读证据 ' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' db-1 ' } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' }));
}
function choose(dialog: ReturnType<typeof within>, label: string) {
  fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
  fireEvent.click(screen.getByText(label, { selector: '.ant-select-item-option-content' }));
}
beforeEach(() => { vi.mocked(getDiagnosticGrants).mockReset(); });

describe('MySQLGrantForm', () => {
  it('shows read-only TLS scope and masked stored secrets', () => {
    const { dialog } = show();
    expect(dialog.queryByText(/仅通过 TLS 读取 MySQL 状态与复制信息/)).not.toBeInTheDocument();
    expect(dialog.getByLabelText('MySQL 密码')).toHaveValue('');
    expect(dialog.getByLabelText('MySQL 密码')).toHaveAttribute('placeholder', '********');
    expect(dialog.getByLabelText('MySQL CA PEM')).toHaveValue('');
    expect(dialog.getByText('留空保留 CA：SHA256:ca')).toBeInTheDocument();
  });

  it('requires exact confirmation and explicit manual approval, trimming only confirmation and reason', async () => {
    const { dialog, onSubmit } = show();
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled(); confirm(dialog);
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'DB-1' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' db-1 ' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' }));
    expect(submit).toBeDisabled();
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' }));
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ kind: 'mysql', asset_id: asset.id, expected_revision: 3,
      confirmation: 'db-1', reason: '采集数据库只读证据', mysql_confirmed: true, action_ids: ['mysql.status'] });
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('password');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ca_pem');
  }, 15_000);

  it('requires password and CA for a first grant and preserves password whitespace', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog, onSubmit } = show(null);
    choose(dialog, 'db-1 · 192.0.2.10');
    await waitFor(() => expect(dialog.getByLabelText('MySQL 专用只读账号')).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('MySQL 专用只读账号'), { target: { value: 'ops_reader' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: 'MySQL 状态' }));
    const expiry = dialog.getByLabelText('授权有效期');
    fireEvent.change(expiry, { target: { value: dayjs().add(1, 'day').format('YYYY-MM-DD HH:mm:ss') } });
    fireEvent.keyDown(expiry, { key: 'Enter', code: 'Enter' });
    confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: '  raw password  ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: ca } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ expected_revision: 0, password: '  raw password  ', ca_pem: ca });
  }, 15_000);

  it.each(['username', 'port', 'host'])('requires a fresh password when %s changes', async (field) => {
    const { dialog, onSubmit } = show(grant, field === 'host' ? [{ ...asset, host: '192.0.2.30' }] : [asset]);
    confirm(dialog);
    if (field === 'username') fireEvent.change(dialog.getByLabelText('MySQL 专用只读账号'), { target: { value: 'other_reader' } });
    if (field === 'port') fireEvent.change(dialog.getByLabelText('MySQL 端口'), { target: { value: '3307' } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'new-password' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ expected_revision: 3, password: 'new-password' });
  });

  it.each(['root', 'admin', 'mysql', 'administrator', 'UPPER', 'a'.repeat(65)])('rejects unsafe MySQL identity: %s', (username) => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('MySQL 专用只读账号'), { target: { value: username } });
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'new-password' } });
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('validates optional database names and the password size without trimming', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    for (const database of ['mysql', 'db-name_$1', 'a'.repeat(64), '']) {
      fireEvent.change(dialog.getByLabelText('默认数据库'), { target: { value: database } });
      await waitFor(() => expect(submit).toBeEnabled());
    }
    for (const database of ['db.name', 'db name', 'db;SELECT 1', 'a'.repeat(65)]) {
      fireEvent.change(dialog.getByLabelText('默认数据库'), { target: { value: database } });
      expect(submit).toBeDisabled();
    }
    fireEvent.change(dialog.getByLabelText('默认数据库'), { target: { value: '' } });
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'p'.repeat(4097) } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'p'.repeat(4096) } });
    await waitFor(() => expect(submit).toBeEnabled());
  });

  it('bounds CA chains to five certificates and checks raw input size', async () => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'ca-rotation-password' } });
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    for (const count of [1, 2, 5]) {
      fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: Array<string>(count).fill(ca).join('\n') } });
      await waitFor(() => expect(submit).toBeEnabled());
    }
    for (const invalid of [Array<string>(6).fill(ca).join('\n'), `${ca}\nnot-a-certificate`, `${ca}${' '.repeat(32768)}`, '   ']) {
      fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: invalid } });
      expect(submit).toBeDisabled();
    }
  });

  it('requires a password when replacing the CA certificate', async () => {
    const { dialog, onSubmit } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: ca } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'raw password ' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({ password: 'raw password ', ca_pem: ca });
  });

  it.each([-1, 91])('blocks an out-of-range expiry (%s days)', (days) => {
    const { dialog } = show({ ...grant, expires_at: dayjs().add(days, 'day').toISOString() }); confirm(dialog);
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('shows unavailable actions but blocks their selection and submission', () => {
    const { dialog } = show(grant, [asset], catalog.map((item) => ({ ...item, available: false }))); confirm(dialog);
    expect(dialog.getByRole('checkbox', { name: 'MySQL 状态' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('clears sensitive values and manual confirmation on asset changes', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'temporary-password' } });
    fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: ca } });
    choose(dialog, 'db-2 · 192.0.2.20');
    expect(dialog.getByLabelText('MySQL 密码')).toHaveValue('');
    expect(dialog.getByLabelText('MySQL CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' })).not.toBeChecked();
    await waitFor(() => expect(dialog.getByLabelText('MySQL 专用只读账号')).toBeEnabled());
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  }, 15_000);

  it('refuses to convert a grant of another kind into MySQL', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 1, items: [{ ...grant, kind: 'linux' }], catalog });
    const { dialog } = show(null);
    choose(dialog, 'db-1 · 192.0.2.10');
    expect(await screen.findByText('该资产已有其他只读授权，不允许转换授权类型')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('keeps a failed asset lookup blocked', async () => {
    vi.mocked(getDiagnosticGrants).mockRejectedValue(new Error('network'));
    const { dialog } = show(null);
    choose(dialog, 'db-1 · 192.0.2.10');
    expect(await screen.findByText('资产授权状态加载失败，请刷新后重试')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('ignores late asset responses after unmount', async () => {
    let resolveOld: ((value: Awaited<ReturnType<typeof getDiagnosticGrants>>) => void) | undefined;
    vi.mocked(getDiagnosticGrants).mockImplementation(() => new Promise((resolve) => { resolveOld = resolve; }));
    const old = show(null); choose(old.dialog, 'db-1 · 192.0.2.10'); old.unmount();
    const next = show(null);
    await act(async () => { resolveOld?.({ total: 1, items: [grant], catalog }); });
    expect(next.dialog.getByLabelText('MySQL 专用只读账号')).toHaveValue('');
    expect(next.onSubmit).not.toHaveBeenCalled();
  });

  it('blocks busy submissions and erases password, CA and confirmation when permission is lost', async () => {
    const { dialog, rerender, component, onSubmit } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('MySQL 密码'), { target: { value: 'draft-password' } });
    fireEvent.change(dialog.getByLabelText('MySQL CA PEM'), { target: { value: ca } });
    rerender(component(true));
    expect(dialog.getByRole('button', { name: /保存只读授权/ })).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: /保存只读授权/ }));
    expect(onSubmit).not.toHaveBeenCalled();
    rerender(component(false, false));
    await waitFor(() => expect(dialog.getByLabelText('MySQL 密码')).toHaveValue(''));
    expect(dialog.getByLabelText('MySQL CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' })).not.toBeChecked();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });
});
