import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ScanTask } from '../types/scan-task';
import { ScanTaskBatchActions, eligibleForScanAction } from './ScanTaskBatchActions';

const queued = { id: 'q1', name: '排队任务', status: 'queued', archived: false } as ScanTask;
const completed = { ...queued, id: 'c1', name: '完成任务', status: 'completed' } as ScanTask;
const archived = { ...completed, id: 'a1', name: '归档任务', archived: true };
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
function show(tasks = [queued, completed, archived]) {
  const onComplete = vi.fn(async (_failed: string[]) => true);
  const onBusyChange = vi.fn();
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <ScanTaskBatchActions tasks={tasks} disabled={false} onComplete={onComplete} onBusyChange={onBusyChange} />
  </ConfigProvider>);
  return { ...view, onComplete, onBusyChange };
}
afterEach(() => vi.unstubAllGlobals());

describe('ScanTaskBatchActions', () => {
  it('limits each action to an eligible lifecycle without deleting evidence', () => {
    expect(eligibleForScanAction(queued, 'start')).toBe(true);
    expect(eligibleForScanAction(completed, 'start')).toBe(false);
    expect(eligibleForScanAction(queued, 'cancel')).toBe(true);
    expect(eligibleForScanAction(completed, 'archive')).toBe(true);
    expect(eligibleForScanAction(queued, 'archive')).toBe(false);
    expect(eligibleForScanAction(archived, 'restore')).toBe(true);
    expect(eligibleForScanAction(archived, 'rerun')).toBe(true);
    expect(eligibleForScanAction(archived, 'start')).toBe(false);
  });
  it('requires confirmation, shows eligible/skipped scope and only posts the eligible start', async () => {
    const fetchMock = vi.fn(async () => response({ ...queued, status: 'scanning' }));
    vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show();
    fireEvent.click(screen.getByRole('button', { name: '批量启动' }));
    const dialog = within(await screen.findByRole('dialog', { name: '批量启动' }));
    expect(dialog.getByText('可操作 1 项，跳过 2 项')).toBeInTheDocument();
    expect(dialog.getByText('排队任务')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: '确认启动' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/q1/start', expect.objectContaining({ method: 'POST' }));
  });
  it('cancels the confirmation without dispatching any request', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock); show();
    fireEvent.click(screen.getByRole('button', { name: '批量归档' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText(/报告和漏洞证据保留/)).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: /返\s*回/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('runs requests sequentially and prevents double confirmation', async () => {
    let resolveFirst: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    const first = new Promise<ReturnType<typeof response>>((resolve) => { resolveFirst = resolve; });
    const fetchMock = vi.fn().mockReturnValueOnce(first).mockResolvedValue(response(completed));
    vi.stubGlobal('fetch', fetchMock);
    const { onBusyChange, onComplete } = show([queued, { ...queued, id: 'q2', name: '第二任务' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量启动' }));
    const confirm = within(await screen.findByRole('dialog')).getByRole('button', { name: '确认启动' });
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onBusyChange).toHaveBeenCalledWith(true);
    await act(async () => { resolveFirst(response(queued)); });
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
  it('reports partial failure and retains failed IDs without retrying or deleting', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(completed))
      .mockResolvedValueOnce(response({ detail: '任务状态已变更' }, 409));
    vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([completed, { ...completed, id: 'c2', name: '冲突任务' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量归档' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认归档' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['c2']));
    expect(await screen.findByText('成功 1 项，失败或未执行 1 项')).toBeInTheDocument();
    expect(screen.getByText('任务状态已变更')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => init.method === 'PATCH' && init.body === '{"archived":true}')).toBe(true);
  });
  it('stops remaining writes when authentication is rejected', async () => {
    const fetchMock = vi.fn(async () => response({ detail: '权限已失效' }, 403));
    vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([completed, { ...completed, id: 'c2' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量归档' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认归档' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['c1', 'c2']));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('stops after an ambiguous network failure without dispatching or retrying remaining tasks', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock); const { onComplete } = show([queued, { ...queued, id: 'q2' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量启动' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认启动' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['q1', 'q2']));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('请求结果待核实，请刷新后确认；未自动重试')).toBeInTheDocument();
  });
  it('restores archived records using the existing endpoint', async () => {
    const fetchMock = vi.fn(async () => response(completed)); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([archived]);
    fireEvent.click(screen.getByRole('button', { name: '批量恢复' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认恢复' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/a1/archive', expect.objectContaining({ method: 'PATCH', body: '{"archived":false}' }));
  });
  it('reruns into a new queued task without automatically starting it', async () => {
    const fetchMock = vi.fn(async () => response({ ...queued, id: 'new-task', rerun_of_id: completed.id }));
    vi.stubGlobal('fetch', fetchMock); const { onComplete } = show([completed]);
    fireEvent.click(screen.getByRole('button', { name: '批量重跑' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText(/不会自动启动扫描/)).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: '确认重跑' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/c1/rerun', expect.objectContaining({ method: 'POST' }));
  });
  it('does not dispatch the next request after unmount', async () => {
    let resolveFirst: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    const fetchMock = vi.fn(() => new Promise<ReturnType<typeof response>>((resolve) => { resolveFirst = resolve; }));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount, onComplete } = show([queued, { ...queued, id: 'q2' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量启动' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认启动' }));
    unmount();
    await act(async () => { resolveFirst(response(queued)); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();
  });
  it('hides actions without selection while preserving an existing result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(completed)));
    const { rerender, onComplete, onBusyChange } = show([completed]);
    fireEvent.click(screen.getByRole('button', { name: '批量归档' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认归档' }));
    await screen.findByText('成功 1 项，失败或未执行 0 项');
    rerender(<ConfigProvider theme={{ token: { motion: false } }}><ScanTaskBatchActions
      tasks={[]} disabled={false} onComplete={onComplete} onBusyChange={onBusyChange} /></ConfigProvider>);
    expect(screen.queryByRole('button', { name: /批量/ })).not.toBeInTheDocument();
    expect(screen.getByText('成功 1 项，失败或未执行 0 项')).toBeInTheDocument();
  });
  it('stops later writes after a server failure', async () => {
    const fetchMock = vi.fn(async () => response({ detail: '服务异常' }, 503)); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([queued, { ...queued, id: 'q2' }]);
    fireEvent.click(screen.getByRole('button', { name: '批量启动' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认启动' }));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['q1', 'q2']));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
