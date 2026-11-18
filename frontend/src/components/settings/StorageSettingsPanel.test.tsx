import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { StorageSettingsPanel } from './StorageSettingsPanel';
import { createSettingsDataJob, getCleanupPreview, getSettingsDataJobs } from '../../api/settingsData';
vi.mock('../../api/settingsData', () => ({
  createSettingsDataJob: vi.fn(), getCleanupPreview: vi.fn(), getSettingsDataJobs: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks(); vi.mocked(getSettingsDataJobs).mockResolvedValue({ items: [] });
  vi.mocked(getCleanupPreview).mockResolvedValue({ retention_days: 30, cutoff_at: '2026-10-17T00:00:00Z', counts: [{ resource: 'monitoring_snapshots', label: '服务器监控历史', count: 8 }], total: 8 });
});
it('previews only and enqueues cleanup only after explicit delete confirmation', async () => {
  render(<ConfigProvider theme={{ token: { motion: false } }}><StorageSettingsPanel tab="cleanup" /></ConfigProvider>);
  await waitFor(() => expect(getSettingsDataJobs).toHaveBeenCalledOnce());
  expect(screen.getByRole('button', { name: '执行清理' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '统计清理范围' }));
  await screen.findByText('服务器监控历史');
  expect(createSettingsDataJob).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '输入 delete 确认清理' }), { target: { value: 'yes' } });
  expect(screen.getByRole('button', { name: '执行清理' })).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '输入 delete 确认清理' }), { target: { value: 'delete' } });
  vi.mocked(createSettingsDataJob).mockResolvedValue({
    id: 'cleanup-1', kind: 'cleanup', status: 'queued', result: {},
    created_at: '2026-11-17T01:00:00Z', started_at: null, finished_at: null, error_code: null,
  });
  fireEvent.click(screen.getByRole('button', { name: '执行清理' }));
  await waitFor(() => expect(createSettingsDataJob).toHaveBeenCalledWith({ kind: 'cleanup', confirmation: 'delete' }));
  expect(await screen.findByText('历史清理已提交后台执行')).toBeInTheDocument();
});
it('does not claim native-engine production recovery from SQLite validation', async () => {
  render(<StorageSettingsPanel tab="backup" />);
  expect(screen.getByText(/生产同引擎恢复演练仍待实施/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '创建备份' })).toBeDisabled();
});
it('ignores a stale cleanup preview after switching to the backup tab', async () => {
  let resolvePreview: ((value: Awaited<ReturnType<typeof getCleanupPreview>>) => void) | undefined;
  vi.mocked(getCleanupPreview).mockImplementationOnce(() => new Promise((resolve) => { resolvePreview = resolve; }));
  const view = render(<StorageSettingsPanel tab="cleanup" />);
  fireEvent.click(screen.getByRole('button', { name: '统计清理范围' }));
  view.rerender(<StorageSettingsPanel tab="backup" />);
  await act(async () => resolvePreview?.({
    retention_days: 30, cutoff_at: '2026-10-17T00:00:00Z',
    counts: [{ resource: 'monitoring_snapshots', label: '旧预览范围', count: 8 }], total: 8,
  }));
  expect(screen.queryByText('旧预览范围')).not.toBeInTheDocument();
  view.rerender(<StorageSettingsPanel tab="cleanup" />);
  expect(screen.getByRole('button', { name: '执行清理' })).toBeDisabled();
});
