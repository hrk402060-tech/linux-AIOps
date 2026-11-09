import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getAssets } from '../../api/assets';
import {
  getDiagnosticGrantAudit, getDiagnosticGrants, saveDiagnosticGrant, revokeDiagnosticGrant,
  testDiagnosticGrant, type DiagnosticAction, type DiagnosticGrant,
} from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { DiagnosticGrantsTab } from './DiagnosticGrantsTab';

vi.mock('../../api/assets', () => ({ getAssets: vi.fn() }));
vi.mock('../../api/diagnostics', async () => {
  const actual = await vi.importActual<typeof import('../../api/diagnostics')>('../../api/diagnostics');
  return { ...actual, getDiagnosticGrants: vi.fn(), getDiagnosticGrantAudit: vi.fn(),
    saveDiagnosticGrant: vi.fn(), revokeDiagnosticGrant: vi.fn(), testDiagnosticGrant: vi.fn() };
});
const asset = {
  id: 'mysql-asset', name: 'mysql-db', host: '192.0.2.10', ssh_port: 22,
  lifecycle_status: 'enabled', credential_id: null, credential: null,
} as Asset;
const catalog: DiagnosticAction[] = [
  { id: 'linux.uptime', label: '运行时间与负载', domain: 'linux', available: true, read_only: true,
    required_permission: 'ssh:observe', status: 'available' },
  { id: 'mysql.status', label: 'MySQL 状态', domain: 'mysql', available: true, read_only: true,
    required_permission: 'mysql:observe', status: 'available' },
  { id: 'mysql.replication', label: 'MySQL 复制状态', domain: 'mysql', available: true, read_only: true,
    required_permission: 'mysql:observe', status: 'available' },
];
const grant: DiagnosticGrant = {
  kind: 'mysql', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 3306, username: 'ops_reader',
  action_ids: ['mysql.status', 'mysql.replication'], expires_at: dayjs().add(1, 'day').toISOString(),
  enabled: true, effective_status: 'active', revision: 3, key_fingerprint: '', host_fingerprint: '',
  secret_configured: true, ca_fingerprint: 'SHA256:mysql-ca',
  mysql_config: { host: asset.host, port: 3306, username: 'ops_reader', database: '' },
  operator: 'admin', updated_at: '2026-10-22T00:00:00Z',
};
const component = (role: 'admin' | 'operator' | 'auditor' = 'admin') =>
  <ConfigProvider theme={{ token: { motion: false } }}><DiagnosticGrantsTab currentUser={{ role }} /></ConfigProvider>;

beforeEach(() => {
  vi.mocked(getAssets).mockReset().mockResolvedValue([asset]);
  vi.mocked(getDiagnosticGrants).mockReset().mockResolvedValue({ total: 1, items: [grant], catalog });
  vi.mocked(getDiagnosticGrantAudit).mockReset().mockResolvedValue({ items: [{
    id: 'audit-mysql', asset_id: asset.id, action: 'created', operator: 'admin',
    reason: 'MySQL 只读授权记录', revision: 3, created_at: '2026-10-22T00:00:00Z',
  }] });
  vi.mocked(saveDiagnosticGrant).mockReset().mockResolvedValue({ ...grant, revision: 4 });
  vi.mocked(revokeDiagnosticGrant).mockReset();
  vi.mocked(testDiagnosticGrant).mockReset();
});

describe('DiagnosticGrantsTab MySQL', () => {
  it('displays the production MySQL catalog and opens the password-only editor', async () => {
    render(component());
    expect(await screen.findByText('192.0.2.10:3306')).toBeInTheDocument();
    expect(screen.getByText('密码：********')).toBeInTheDocument();
    expect(screen.getByText('SHA256:mysql-ca')).toBeInTheDocument();
    expect(screen.getByText('MySQL 状态')).toBeInTheDocument();
    expect(screen.getByText('MySQL 复制状态')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '配置 mysql-db 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 MySQL 只读授权' }));
    expect(dialog.getByLabelText('MySQL 专用只读账号')).toHaveValue('ops_reader');
    expect(dialog.getByLabelText('MySQL 密码')).toHaveValue('');
    expect(dialog.getByLabelText('MySQL CA PEM')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: 'MySQL 状态' })).toBeChecked();
    expect(dialog.queryByLabelText(/私钥|Token/i)).not.toBeInTheDocument();
    expect(dialog.queryByRole('radio', { name: 'Linux' })).not.toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '保留只读采集范围' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' mysql-db ' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 MySQL 只读权限' }));
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled()); fireEvent.click(submit);
    await waitFor(() => expect(saveDiagnosticGrant).toHaveBeenCalledOnce());
    expect(vi.mocked(saveDiagnosticGrant).mock.calls[0][0]).toMatchObject({
      kind: 'mysql', expected_revision: 3, confirmation: 'mysql-db', mysql_confirmed: true,
    });
    expect(vi.mocked(saveDiagnosticGrant).mock.calls[0][0]).not.toHaveProperty('private_key');
    expect(vi.mocked(saveDiagnosticGrant).mock.calls[0][0]).not.toHaveProperty('token');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  }, 15_000);

  it('provides a MySQL create entry with only the applicable catalog actions', async () => {
    render(component());
    const create = await screen.findByRole('button', { name: '新增只读授权' });
    await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
    fireEvent.click(screen.getByRole('radio', { name: 'MySQL' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 MySQL 只读授权' }));
    expect(dialog.getByRole('checkbox', { name: 'MySQL 状态' })).toBeEnabled();
    expect(dialog.getByRole('checkbox', { name: 'MySQL 复制状态' })).toBeEnabled();
    expect(dialog.queryByRole('checkbox', { name: '运行时间与负载' })).not.toBeInTheDocument();
    expect(dialog.getByLabelText('MySQL 密码')).toBeInTheDocument();
    expect(dialog.queryByLabelText(/私钥|Token/i)).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
    expect(saveDiagnosticGrant).not.toHaveBeenCalled();
  }, 15_000);

  it('keeps operator and auditor access read-only while retaining authorization history', async () => {
    const view = render(component('operator'));
    await screen.findByText('ops_reader');
    const expectReadOnly = () => {
      for (const name of ['新增只读授权', '配置 mysql-db 只读授权', '验证 mysql-db 只读授权', '撤销 mysql-db 只读授权']) {
        expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
      }
    };
    expectReadOnly();
    view.rerender(component('auditor')); expectReadOnly();
    fireEvent.click(screen.getByRole('button', { name: '查看 mysql-db 授权记录' }));
    expect(await screen.findByText('MySQL 只读授权记录')).toBeInTheDocument();
    expect(getDiagnosticGrantAudit).toHaveBeenCalledWith(asset.id);
    expect(saveDiagnosticGrant).not.toHaveBeenCalled();
    expect(revokeDiagnosticGrant).not.toHaveBeenCalled();
    expect(testDiagnosticGrant).not.toHaveBeenCalled();
  });
});
