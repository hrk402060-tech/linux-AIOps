import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getDiagnosticGrants, type DiagnosticGrant } from '../../api/diagnostics';
import type { Asset } from '../../types/asset';
import { RabbitMQGrantForm } from './RabbitMQGrantForm';

vi.mock('../../api/diagnostics', () => ({ getDiagnosticGrants: vi.fn() }));
const asset = { id: 'asset-1', name: 'mq-1', host: '192.0.2.10', lifecycle_status: 'enabled' } as Asset;
const ca = '-----BEGIN CERTIFICATE-----\nVEVTVC1PTkxZ\n-----END CERTIFICATE-----';
const catalog = ['status', 'nodes', 'queues', 'consumers'].map((id) => ({
  id: `rabbitmq.${id}`, label: `RabbitMQ ${id}`, domain: 'rabbitmq', available: true, read_only: true,
  required_permission: 'rabbitmq:observe', status: 'available',
}));
const grant: DiagnosticGrant = {
  kind: 'rabbitmq', asset_id: asset.id, asset_name: asset.name, host: asset.host, port: 15671,
  username: 'ops_reader', action_ids: ['rabbitmq.status'], expires_at: dayjs().add(1, 'day').toISOString(),
  enabled: true, effective_status: 'active', revision: 3, key_fingerprint: '', host_fingerprint: '',
  secret_configured: true, ca_fingerprint: 'SHA256:ca',
  rabbitmq_config: { host: asset.host, port: 15671, username: 'ops_reader', vhost: '/' },
  operator: 'admin', updated_at: '2026-10-22T00:00:00Z',
};
function show(initialGrant: DiagnosticGrant | null = grant, actions = catalog) {
  const onSubmit = vi.fn();
  const component = (canSubmit = true) => <ConfigProvider theme={{ token: { motion: false } }}>
    <RabbitMQGrantForm assets={[asset]} catalog={actions} initialGrant={initialGrant}
      busy={false} canSubmit={canSubmit} onCancel={vi.fn()} onSubmit={onSubmit} />
  </ConfigProvider>;
  return { ...render(component()), component, onSubmit,
    dialog: within(screen.getByRole('dialog', { name: '配置 RabbitMQ 只读授权' })) };
}
function confirm(dialog: ReturnType<typeof within>) {
  fireEvent.change(dialog.getByLabelText('授权原因'), { target: { value: ' 采集消息队列只读证据 ' } });
  fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' mq-1 ' } });
  fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 RabbitMQ 只读权限' }));
}
beforeEach(() => { vi.mocked(getDiagnosticGrants).mockReset(); });

describe('RabbitMQGrantForm', () => {
  it('shows the fixed HTTPS scope and masks existing credentials', () => {
    const { dialog } = show();
    expect(dialog.getByText(/仅通过 HTTPS Management API 的固定 GET/)).toBeInTheDocument();
    expect(dialog.getByLabelText('RabbitMQ 密码')).toHaveValue('');
    expect(dialog.getByLabelText('RabbitMQ 密码')).toHaveAttribute('placeholder', '********');
    expect(dialog.getByLabelText('RabbitMQ CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('RabbitMQ Management API 端口')).toHaveValue('15671');
    expect(dialog.getByLabelText('RabbitMQ vhost')).toHaveValue('/');
    expect(dialog.getByText('留空保留 CA：SHA256:ca')).toBeInTheDocument();
  });

  it('requires exact trimmed confirmation and explicit human approval before saving', async () => {
    const { dialog, onSubmit } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: 'MQ-1' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('资产名称确认'), { target: { value: ' mq-1 ' } });
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 RabbitMQ 只读权限' }));
    expect(submit).toBeDisabled();
    fireEvent.click(dialog.getByRole('checkbox', { name: '已人工确认 RabbitMQ 只读权限' }));
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      kind: 'rabbitmq', expected_revision: 3, vhost: '/', confirmation: 'mq-1',
      reason: '采集消息队列只读证据', rabbitmq_confirmed: true,
    });
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('password');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ca_pem');
  });

  it('requires new secrets for an initial grant and preserves password whitespace', async () => {
    vi.mocked(getDiagnosticGrants).mockResolvedValue({ total: 0, items: [], catalog });
    const { dialog, onSubmit } = show(null);
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '授权服务器' }));
    fireEvent.click(screen.getByText('mq-1 · 192.0.2.10', { selector: '.ant-select-item-option-content' }));
    await waitFor(() => expect(dialog.getByLabelText('RabbitMQ 专用只读账号')).toBeEnabled());
    fireEvent.click(dialog.getByRole('checkbox', { name: 'RabbitMQ status' }));
    const expiry = dialog.getByLabelText('授权有效期');
    fireEvent.change(expiry, { target: { value: dayjs().add(1, 'day').format('YYYY-MM-DD HH:mm:ss') } });
    fireEvent.keyDown(expiry, { key: 'Enter', code: 'Enter' }); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('RabbitMQ 密码'), { target: { value: ' raw password ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('RabbitMQ CA PEM'), { target: { value: ca } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      expected_revision: 0, password: ' raw password ', ca_pem: ca, port: 15671, vhost: '/',
    });
  });

  it('requires a fresh password after changing the vhost or CA', async () => {
    const { dialog } = show(); confirm(dialog);
    const submit = dialog.getByRole('button', { name: '保存只读授权' });
    fireEvent.change(dialog.getByLabelText('RabbitMQ vhost'), { target: { value: ' /operations ' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('RabbitMQ 密码'), { target: { value: 'fresh-password' } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.change(dialog.getByLabelText('RabbitMQ CA PEM'), { target: { value: 'invalid CA' } });
    expect(submit).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('RabbitMQ CA PEM'), { target: { value: ca } });
    await waitFor(() => expect(submit).toBeEnabled());
  });

  it.each(['root', 'admin', 'administrator', 'guest', 'UPPER'])('rejects unsafe account %s', (username) => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('RabbitMQ 专用只读账号'), { target: { value: username } });
    fireEvent.change(dialog.getByLabelText('RabbitMQ 密码'), { target: { value: 'fresh-password' } });
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('blocks invalid vhosts and unavailable actions', () => {
    const { dialog } = show(grant, catalog.map((item) => ({ ...item, available: false }))); confirm(dialog);
    expect(dialog.getByRole('checkbox', { name: 'RabbitMQ status' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it.each(['', 'bad\u0001host', 'v'.repeat(129)])('blocks invalid vhost %s', (value) => {
    const { dialog } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('RabbitMQ vhost'), { target: { value } });
    fireEvent.change(dialog.getByLabelText('RabbitMQ 密码'), { target: { value: 'fresh-password' } });
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
  });

  it('erases password, CA and human confirmation when permission is lost', async () => {
    const { dialog, rerender, component, onSubmit } = show(); confirm(dialog);
    fireEvent.change(dialog.getByLabelText('RabbitMQ 密码'), { target: { value: 'draft-password' } });
    fireEvent.change(dialog.getByLabelText('RabbitMQ CA PEM'), { target: { value: ca } });
    rerender(component(false));
    await waitFor(() => expect(dialog.getByLabelText('RabbitMQ 密码')).toHaveValue(''));
    expect(dialog.getByLabelText('RabbitMQ CA PEM')).toHaveValue('');
    expect(dialog.getByLabelText('资产名称确认')).toHaveValue('');
    expect(dialog.getByRole('checkbox', { name: '已人工确认 RabbitMQ 只读权限' })).not.toBeChecked();
    expect(dialog.getByRole('button', { name: '保存只读授权' })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
