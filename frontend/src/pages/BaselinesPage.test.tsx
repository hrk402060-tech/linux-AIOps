import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { BaselinesPage } from './BaselinesPage';

afterEach(() => vi.unstubAllGlobals());
it('shows read-only baseline evidence and compliance rate', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [{ id: 'base-1', name: '生产基线', status: 'completed', progress: 100, read_only: true, policy_id: 'linux-security-baseline', policy_version: '2026.09', asset_ids: ['a1'], asset_snapshot: [{ id: 'a1', name: 'node-1', host: '192.0.2.1', os_family: 'openeuler' }], summary: { assets_total: 1, assets_checked: 1, passed: 4, failed: 1, compliance_rate: 80 }, results: [], execution_log: [], error_message: null, rerun_of_id: null, started_at: '', finished_at: '', created_at: '', updated_at: '' }] }).mockResolvedValueOnce({ ok: true, json: async () => [] }));
  render(<ConfigProvider><BaselinesPage /></ConfigProvider>);
  expect(await screen.findByText('生产基线')).toBeInTheDocument();
  expect(screen.getAllByText('80%')).toHaveLength(2);
  expect(screen.getByText('只读')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: 'Linux 基线与合规' })).toBeInTheDocument();
});
it('keeps baseline actions available without a duplicate embedded heading', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><BaselinesPage embedded /></ConfigProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const page = within(screen.getByRole('region', { name: '基线合规' }));
  expect(page.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  expect(page.queryByText('SSH 无 Agent 只读检查')).not.toBeInTheDocument();
  fireEvent.click(page.getByRole('button', { name: '刷新基线任务' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  fireEvent.click(page.getByRole('button', { name: '新建检查' }));
  const dialog = within(await screen.findByRole('dialog', { name: '新建基线检查' }));
  expect(dialog.getByText('目标服务器')).toBeInTheDocument();
  expect(dialog.getByText('仅执行内置 SSH 只读检查，不下发修复命令')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(fetchMock.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
});
