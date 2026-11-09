import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getAssets } from '../../api/assets';
import {
  getDiagnosticGrants, saveDiagnosticGrant, type DiagnosticAction, type DiagnosticGrant,
} from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { DiagnosticGrantsTab } from './DiagnosticGrantsTab';

vi.mock('../../api/assets', () => ({ getAssets: vi.fn() }));
vi.mock('../../api/diagnostics', async () => ({
  ...(await vi.importActual<typeof import('../../api/diagnostics')>('../../api/diagnostics')),
  getDiagnosticGrants: vi.fn(), saveDiagnosticGrant: vi.fn(),
}));
const asset = { id: 'mq-asset', name: 'mq-1', host: '192.0.2.10', lifecycle_status: 'enabled' } as Asset;
const catalog: DiagnosticAction[] = ['status', 'nodes', 'queues', 'consumers'].map((id) => ({
  id: `rabbitmq.${id}`, label: `RabbitMQ ${id}`, domain: 'rabbitmq', available: true,
  read_only: true, required_permission: 'rabbitmq:observe', status: 'available',
}));
const grant: DiagnosticGrant = {
  kind: 'rabbitmq', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 15671,
  username: 'ops_reader', action_ids: ['rabbitmq.status'], expires_at: dayjs().add(1, 'day').toISOString(),
  enabled: true, effective_status: 'active', revision: 3, key_fingerprint: '', host_fingerprint: '',
  secret_configured: true, ca_fingerprint: 'SHA256:mq-ca',
  rabbitmq_config: { host: asset.host, port: 15671, username: 'ops_reader', vhost: '/production' },
  operator: 'admin', updated_at: '2026-10-22T00:00:00Z',
};
const component = (role: 'admin' | 'operator' = 'admin') =>
  <ConfigProvider theme={{ token: { motion: false } }}><DiagnosticGrantsTab currentUser={{ role }} /></ConfigProvider>;
beforeEach(() => {
  vi.mocked(getAssets).mockReset().mockResolvedValue([asset]);
  vi.mocked(getDiagnosticGrants).mockReset().mockResolvedValue({ total: 1, items: [grant], catalog });
  vi.mocked(saveDiagnosticGrant).mockReset().mockResolvedValue({ ...grant, revision: 4 });
});

describe('DiagnosticGrantsTab RabbitMQ', () => {
  it('renders HTTPS target, vhost and masks secrets, and preserves grant revision on manual save', async () => {
    render(component());
    expect(await screen.findByText('https://192.0.2.10:15671')).toBeInTheDocument();
    expect(screen.getByText('vhost：/production')).toBeInTheDocument();
    expect(screen.getByText('密码：********')).toBeInTheDocument();
    expect(screen.getByText('SHA256:mq-ca')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '配置 mq-1 只读授权' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 RabbitMQ 只读授权' }));
    expect(dialog.getByLabelText('RabbitMQ vhost')).toHaveValue('/production');
    expect(dialog.getByLabelText('RabbitMQ 密码')).toHaveValue('');
    expect(dialog.queryByRole('radio', { name: 'Linux' })).not.toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: '保留只读采集范围' } });
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' mq-1 ' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 RabbitMQ 只读权限' }));
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled()); fireEvent.click(submit);
    await waitFor(() => expect(saveDiagnosticGrant).toHaveBeenCalledOnce());
    expect(vi.mocked(saveDiagnosticGrant).mock.calls[0][0]).toMatchObject({
      kind: 'rabbitmq', expected_revision: 3, confirmation: 'mq-1', rabbitmq_confirmed: true,
    });
    expect(vi.mocked(saveDiagnosticGrant).mock.calls[0][0]).not.toHaveProperty('password');
  });

  it('offers RabbitMQ creation without adding unrelated actions', async () => {
    render(component());
    const create = await screen.findByRole('button', { name: '新增只读授权' });
    await waitFor(() => expect(create).toBeEnabled()); fireEvent.click(create);
    fireEvent.click(screen.getByRole('radio', { name: 'RabbitMQ' }));
    const dialog = within(screen.getByRole('dialog', { name: '配置 RabbitMQ 只读授权' }));
    expect(dialog.getByLabelText('RabbitMQ Management API 端口')).toHaveValue('15671');
    for (const id of ['status', 'nodes', 'queues', 'consumers']) {
      expect(dialog.getByRole('checkbox', { name: `RabbitMQ ${id}` })).toBeEnabled();
    }
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
    expect(saveDiagnosticGrant).not.toHaveBeenCalled();
  });

  it('keeps non-admin users read-only', async () => {
    render(component('operator'));
    await screen.findByText('ops_reader');
    expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置 mq-1 只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '验证 mq-1 只读授权' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '撤销 mq-1 只读授权' })).not.toBeInTheDocument();
    expect(saveDiagnosticGrant).not.toHaveBeenCalled();
  });
});
