import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { TerminalAssetCatalog } from './TerminalAssetCatalog';
import type { Asset } from '../types/asset';

const asset: Asset = {
  id: 'node-1', name: 'server-one', host: '192.0.2.10', ssh_port: 22,
  os_family: 'openeuler', environment: 'production', group_name: '生产分组',
  credential_id: 'credential-1', credential: { id: 'credential-1', name: 'SSH', username: 'ops', credential_type: 'password' },
  labels: [], status: 'online', lifecycle_status: 'enabled', lifecycle_reason: null,
  lifecycle_operator: null, archived_at: null, latency_ms: null, check_message: null,
  last_checked_at: null, created_at: '', updated_at: '',
};
const other: Asset = { ...asset, id: 'node-2', name: 'server-two', host: '192.0.2.11', group_name: 'all', status: 'unknown' };

describe('TerminalAssetCatalog', () => {
  it('combines group and case-insensitive search filters and resets on all servers', async () => {
    render(<TerminalAssetCatalog assets={[asset, other]} loading={false} error="" onReload={vi.fn()} onOpenInPage={vi.fn()} />);
    await userEvent.click(screen.getByText('生产分组 (1)', { exact: true }));
    expect(screen.queryByRole('link', { name: '新标签连接 server-two' })).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: '搜索服务器' }), '  SERVER-ONE  ');
    expect(screen.getByRole('link', { name: '新标签连接 server-one' })).toBeInTheDocument();
    await userEvent.click(screen.getByText('all (1)', { exact: true }));
    expect(screen.getByText('没有匹配的服务器')).toBeInTheDocument();
    await userEvent.clear(screen.getByRole('textbox', { name: '搜索服务器' }));
    expect(screen.getByRole('link', { name: '新标签连接 server-two' })).toBeInTheDocument();
    await userEvent.click(screen.getByText('全部服务器 (2)', { exact: true }));
    expect(screen.getByRole('link', { name: '新标签连接 server-one' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '新标签连接 server-two' })).toBeInTheDocument();
  }, 15_000);

  it('uses safe asset-only URLs and explicit inline fallback without exposing credentials', async () => {
    const onOpen = vi.fn();
    render(<TerminalAssetCatalog assets={[asset]} loading={false} error="" onReload={vi.fn()} onOpenInPage={onOpen} />);
    const link = screen.getByRole('link', { name: '新标签连接 server-one' });
    expect(link).toHaveAttribute('href', `${window.location.origin}/?terminal_asset=node-1`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    await userEvent.click(screen.getByRole('button', { name: '当前页打开 server-one' }));
    expect(onOpen).toHaveBeenCalledWith('node-1');
    expect(screen.queryByText('credential-1')).not.toBeInTheDocument();
  });

  it('paginates rows and distinguishes unknown, offline and missing-login status', async () => {
    const assets = Array.from({ length: 16 }, (_, index) => ({ ...asset, id: `id-${index}`, name: `server-${index}` }));
    assets[0] = { ...assets[0], status: 'unknown' };
    assets[1] = { ...assets[1], status: 'offline' };
    assets[2] = { ...assets[2], credential: null, credential_id: null };
    render(<TerminalAssetCatalog assets={assets} loading={false} error="" onReload={vi.fn()} onOpenInPage={vi.fn()} />);
    expect(within(screen.getByRole('table', { name: 'SSH 服务器资源' })).getAllByRole('row')).toHaveLength(16);
    expect(screen.getByText('待检查')).toBeInTheDocument();
    expect(screen.getByText('离线')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新标签连接 server-2' })).toBeDisabled();
    await userEvent.click(screen.getByTitle('2', { exact: true }));
    expect(screen.getByRole('link', { name: '新标签连接 server-15' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '新标签连接 server-0' })).not.toBeInTheDocument();
  }, 15_000);
});
