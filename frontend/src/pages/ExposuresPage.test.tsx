import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { ExposuresPage } from './ExposuresPage';

afterEach(() => vi.unstubAllGlobals());
it('shows local listener scope without claiming internet reachability', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [{ id: 'exp-1', name: '生产监听检查', status: 'completed', progress: 100, read_only: true, methodology_version: '2026.09', asset_ids: ['a1'], asset_snapshot: [{ id: 'a1', name: 'node-1', host: '192.0.2.1', os_family: 'openeuler' }], summary: { assets_total: 1, assets_checked: 1, listeners: 5, potential_exposures: 2, loopback_only: 3 }, results: [], execution_log: [], error_message: null, rerun_of_id: null, started_at: '', finished_at: '', created_at: '', updated_at: '' }] }).mockResolvedValueOnce({ ok: true, json: async () => [] }));
  render(<ConfigProvider><ExposuresPage /></ConfigProvider>);
  expect(await screen.findByText('生产监听检查')).toBeInTheDocument();
  expect(screen.getByText('5 / 2')).toBeInTheDocument();
  expect(screen.getByText('只读')).toBeInTheDocument();
  expect(screen.queryByText('互联网已暴露')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: '服务器暴露面' })).toBeInTheDocument();
});
it('keeps exposure actions available without a duplicate embedded heading', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><ExposuresPage embedded /></ConfigProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const page = within(screen.getByRole('region', { name: '暴露面检测' }));
  expect(page.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  expect(page.queryByText('SSH 无 Agent 只读采集')).not.toBeInTheDocument();
  fireEvent.click(page.getByRole('button', { name: '刷新暴露面任务' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  fireEvent.click(page.getByRole('button', { name: '新建检测' }));
  const dialog = within(await screen.findByRole('dialog', { name: '新建暴露面检测' }));
  expect(dialog.getByText('目标服务器')).toBeInTheDocument();
  expect(dialog.getByText('只采集本机监听快照，不执行外部端口扫描')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(fetchMock.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
});
