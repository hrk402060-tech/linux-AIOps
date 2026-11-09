import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { describe, expect, it, vi } from 'vitest';

import type { Asset } from '../types/asset';
import { AssetFormDrawer } from './AssetFormDrawer';

const existing: Asset = {
  id: 'asset-1', name: 'existing-node', host: '192.0.2.10', ssh_port: 22,
  os_family: 'auto', environment: 'test', group_name: 'test', labels: [],
  credential_id: 'internal-auth', credential: {
    id: 'internal-auth', name: 'internal', username: 'ops', credential_type: 'password',
  },
  status: 'unknown', lifecycle_status: 'enabled', lifecycle_reason: null,
  lifecycle_operator: null, archived_at: null, latency_ms: null,
  check_message: null, last_checked_at: null, created_at: '', updated_at: '',
};

function show(asset: Asset | null = null) {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <AssetFormDrawer open asset={asset} submitting={false} onClose={vi.fn()} onSubmit={onSubmit} />
  </ConfigProvider>);
  return { onSubmit, dialog: within(screen.getByRole('dialog')) };
}

describe('AssetFormDrawer direct SSH login', () => {
  it('requires a username and secret for a new server', async () => {
    const { dialog, onSubmit } = show();
    fireEvent.click(dialog.getByRole('button', { name: /保\s*存/ }));
    expect(await dialog.findByText('请输入 SSH 用户名')).toBeInTheDocument();
    expect(await dialog.findByText('请输入 SSH 密码或私钥')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits a private key directly without choosing a saved credential', async () => {
    const { dialog, onSubmit } = show();
    fireEvent.change(dialog.getByLabelText('资产名称'), { target: { value: 'key-node' } });
    fireEvent.change(dialog.getByLabelText('主机地址'), { target: { value: '192.0.2.11' } });
    fireEvent.change(dialog.getByLabelText('SSH 用户名'), { target: { value: 'ops' } });
    await userEvent.click(dialog.getByLabelText('SSH 认证方式'));
    await userEvent.click(await screen.findByText('私钥', { selector: '.ant-select-item-option-content' }));
    const secret = '-----BEGIN OPENSSH PRIVATE KEY-----\ntest-only-material\n-----END OPENSSH PRIVATE KEY-----\n';
    fireEvent.change(dialog.getByLabelText('SSH 私钥'), { target: { value: secret } });
    fireEvent.click(dialog.getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      ssh_username: 'ops', ssh_credential_type: 'private_key', ssh_secret: secret, credential_id: null,
    }));
  });

  it('keeps existing login information when editing only asset metadata', async () => {
    const { dialog, onSubmit } = show(existing);
    fireEvent.change(dialog.getByLabelText('资产名称'), { target: { value: 'renamed-node' } });
    fireEvent.click(dialog.getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toEqual(expect.objectContaining({ name: 'renamed-node', credential_id: null }));
    expect(payload).not.toHaveProperty('ssh_secret');
    expect(payload).not.toHaveProperty('ssh_username');
    expect(dialog.queryByDisplayValue('ops')).not.toBeInTheDocument();
  });

  it('omits cleared login drafts instead of submitting empty credentials on edit', async () => {
    const { dialog, onSubmit } = show(existing);
    fireEvent.change(dialog.getByLabelText(/^SSH 用户名/), { target: { value: 'draft' } });
    fireEvent.change(dialog.getByLabelText(/^SSH 用户名/), { target: { value: '' } });
    fireEvent.change(dialog.getByLabelText(/^SSH 密码/), { target: { value: 'draft-secret' } });
    fireEvent.change(dialog.getByLabelText(/^SSH 密码/), { target: { value: '' } });
    fireEvent.click(dialog.getByRole('button', { name: /保\s*存/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ssh_secret');
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('ssh_username');
  });

  it('requires both login fields when replacing authentication on edit', async () => {
    const { dialog, onSubmit } = show(existing);
    fireEvent.change(dialog.getByLabelText(/^SSH 用户名/), { target: { value: 'new-ops' } });
    fireEvent.click(dialog.getByRole('button', { name: /保\s*存/ }));
    expect(await dialog.findByText('请输入 SSH 密码或私钥')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
