import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { ScanSchedulesPage } from './ScanSchedulesPage';

afterEach(() => vi.unstubAllGlobals());
it('shows schedule timing and read-only policy', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [{
    id: 'schedule-1', name: '每日巡检', scan_type: 'comprehensive', asset_ids: ['asset-1'],
    asset_snapshot: [{ name: 'node-1' }], schedule_kind: 'daily', daily_time: '02:30',
    interval_hours: null, timezone: 'Asia/Shanghai', enabled: true, last_run_at: null,
    next_run_at: '2026-09-10T02:30:00Z', last_task_id: null, created_at: '', updated_at: '',
  }] }).mockResolvedValueOnce({ ok: true, json: async () => [] }));
  render(<ConfigProvider><ScanSchedulesPage /></ConfigProvider>);
  expect(await screen.findByText('每日巡检')).toBeInTheDocument();
  expect(screen.getByText('每天 02:30')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: '定时扫描' })).toBeInTheDocument();
});
it('keeps schedule actions available without a duplicate embedded heading', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><ScanSchedulesPage embedded /></ConfigProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const page = within(screen.getByRole('region', { name: '定时扫描' }));
  expect(page.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  expect(page.queryByText('只读周期检测')).not.toBeInTheDocument();
  fireEvent.click(page.getByRole('button', { name: '刷新计划' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  fireEvent.click(page.getByRole('button', { name: '新建计划' }));
  const dialog = within(await screen.findByRole('dialog', { name: '新建扫描计划' }));
  expect(dialog.getByText('目标服务器')).toBeInTheDocument();
  expect(dialog.getByText('所有计划仅创建只读扫描任务')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(fetchMock.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
});
