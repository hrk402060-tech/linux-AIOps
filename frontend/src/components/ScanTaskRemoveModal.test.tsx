import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ScanTask } from '../types/scan-task';
import { ScanTaskRemoveModal } from './ScanTaskRemoveModal';

const completed = { id: 'c1', name: '已完成任务', status: 'completed', archived: false } as ScanTask;
const queued = { ...completed, id: 'q1', name: '排队任务', status: 'queued' } as ScanTask;
const response = (status = 204, detail = '') => ({ ok: status < 400, status,
  json: async () => { if (status === 204) throw new Error('No JSON body'); return { detail }; } });
function show(tasks = [completed]) {
  const onComplete = vi.fn(async (_failed: string[]) => true);
  const onBusyChange = vi.fn(), onClose = vi.fn();
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <ScanTaskRemoveModal tasks={tasks} open onClose={onClose} onComplete={onComplete} onBusyChange={onBusyChange} />
  </ConfigProvider>);
  return { ...view, onComplete, onBusyChange, onClose };
}
async function fill(confirmation = '删除 1 项任务', reason = '不再保留此任务') {
  const dialog = within(await screen.findByRole('dialog', { name: '删除扫描任务' }));
  fireEvent.change(dialog.getByLabelText('删除原因'), { target: { value: reason } });
  fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: confirmation } });
  return dialog.getByRole('button', { name: '确认删除' });
}
afterEach(() => vi.unstubAllGlobals());

describe('ScanTaskRemoveModal', () => {
  it('shows the exact confirmation, terminal scope and preserved evidence', async () => {
    const fetchMock = vi.fn(async () => response()); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([completed, queued]);
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('可删除 1 项，跳过 1 项')).toBeInTheDocument();
    expect(dialog.getByText('删除 1 项任务')).toBeInTheDocument();
    expect(dialog.getByText('删除原因需 3 至 1000 个字符，首尾空格不计入。')).toBeInTheDocument();
    expect(dialog.getByText('已完成任务')).toBeInTheDocument();
    expect(dialog.queryByText('排队任务')).not.toBeInTheDocument();
    expect(dialog.getByText(/漏洞、报告和历史证据保留/)).toBeInTheDocument();
    expect(await fill('DELETE 1')).toBeDisabled();
    expect(dialog.getByText('确认文本不匹配，请输入“删除 1 项任务”。')).toBeInTheDocument();
    expect(await fill('删除 2 项任务')).toBeDisabled();
    expect(await fill('删除 1 项任务', '1')).toBeDisabled();
    expect(dialog.getByText('删除原因不足 3 个字符，请补充说明。')).toBeInTheDocument();
    expect(dialog.getByLabelText('删除原因')).toHaveAttribute('aria-invalid', 'true');
    expect(await fill('删除 1 项任务', ' x ')).toBeDisabled();
    expect(await fill('删除 1 项任务', 'x'.repeat(1001))).toBeDisabled();
    expect(dialog.getByText('删除原因不能超过 1000 个字符。')).toBeInTheDocument();
    const confirm = await fill('  删除 1 项任务  ', '  任务已完成  ');
    expect(dialog.queryByText('删除原因不足 3 个字符，请补充说明。')).not.toBeInTheDocument();
    expect(confirm).toBeEnabled(); expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['q1']));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/c1', expect.objectContaining({
      method: 'DELETE', body: JSON.stringify({ confirmation: 'DELETE c1', reason: '任务已完成' }),
    }));
    expect(await screen.findByText('已删除 1 项，失败或未执行 0 项')).toBeInTheDocument();
  });
  it('does not dispatch when cancelled or all tasks are nonterminal', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { onClose } = show([queued]);
    expect(await fill('删除 0 项任务')).toBeDisabled();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /返\s*回/ }));
    expect(onClose).toHaveBeenCalledOnce(); expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each(['failed', 'cancelled'] as const)('allows the %s terminal lifecycle', async (status) => {
    const fetchMock = vi.fn(async () => response()); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([{ ...completed, status }]);
    fireEvent.click(await fill());
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('disables deletion for more than 100 selected tasks', async () => {
    vi.stubGlobal('fetch', vi.fn());
    show(Array.from({ length: 101 }, (_, index) => ({ ...completed, id: 'c' + index })));
    expect(await fill('删除 101 项任务')).toBeDisabled();
    expect(screen.getByText('每次最多选择 100 项')).toBeInTheDocument();
  });
  it('does not delete an empty selection', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock); show([]);
    expect(await fill('删除 0 项任务')).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('sends serial requests and synchronously prevents double submission', async () => {
    let resolveFirst: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    const first = new Promise<ReturnType<typeof response>>((resolve) => { resolveFirst = resolve; });
    const fetchMock = vi.fn().mockReturnValueOnce(first).mockResolvedValue(response()); vi.stubGlobal('fetch', fetchMock);
    const { onComplete, onBusyChange, onClose } = show([completed, { ...completed, id: 'c2' }]);
    const confirm = await fill('删除 2 项任务'); fireEvent.click(confirm); fireEvent.click(confirm);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { resolveFirst(response()); });
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith([]));
    expect(fetchMock).toHaveBeenCalledTimes(2); expect(onClose).toHaveBeenCalledOnce();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });
  it('keeps conflicts selected and proceeds only after a definite rejection', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(409, '任务尚未结束')).mockResolvedValueOnce(response());
    vi.stubGlobal('fetch', fetchMock); const { onComplete } = show([completed, { ...completed, id: 'c2' }]);
    fireEvent.click(await fill('删除 2 项任务'));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['c1']));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(await screen.findByText('已删除 1 项，失败或未执行 1 项')).toBeInTheDocument();
    expect(screen.getByText('任务尚未结束')).toBeInTheDocument();
  });
  it.each([401, 403, 500])('stops after HTTP %s without retrying later writes', async (status) => {
    const fetchMock = vi.fn(async () => response(status, '请求已中止')); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([completed, { ...completed, id: 'c2' }]);
    fireEvent.click(await fill('删除 2 项任务'));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['c1', 'c2']));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText('批量删除已中止，未执行')).toBeInTheDocument();
  });
  it('stops on ambiguous network failure and never retries', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch')); vi.stubGlobal('fetch', fetchMock);
    const { onComplete } = show([completed, { ...completed, id: 'c2' }]);
    fireEvent.click(await fill('删除 2 项任务'));
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(['c1', 'c2']));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText('请求结果待核实，请刷新后确认；未自动重试')).toBeInTheDocument();
  });
  it('retains results and warns when the authoritative list cannot be refreshed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response())); const { onComplete } = show();
    onComplete.mockResolvedValue(false); fireEvent.click(await fill());
    expect(await screen.findByText('列表刷新失败，请刷新后核对状态，勿重复提交已成功任务。')).toBeInTheDocument();
  });
  it('does not dispatch later writes or refresh after unmount', async () => {
    let resolveFirst: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    const fetchMock = vi.fn(() => new Promise<ReturnType<typeof response>>((resolve) => { resolveFirst = resolve; }));
    vi.stubGlobal('fetch', fetchMock); const { unmount, onComplete } = show([completed, { ...completed, id: 'c2' }]);
    fireEvent.click(await fill('删除 2 项任务')); unmount();
    await act(async () => { resolveFirst(response()); });
    expect(fetchMock).toHaveBeenCalledTimes(1); expect(onComplete).not.toHaveBeenCalled();
  });
  it('requires fresh human confirmation after closing and reopening', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { rerender, onClose, onComplete, onBusyChange } = show();
    expect(await fill()).toBeEnabled();
    const modal = (open: boolean) => <ConfigProvider theme={{ token: { motion: false } }}> 
      <ScanTaskRemoveModal tasks={[completed]} open={open} onClose={onClose} onComplete={onComplete} onBusyChange={onBusyChange} />
    </ConfigProvider>;
    rerender(modal(false)); rerender(modal(true));
    const dialog = within(await screen.findByRole('dialog')); 
    expect(dialog.getByLabelText('确认文本')).toHaveValue('');
    expect(dialog.getByLabelText('删除原因')).toHaveValue('');
    expect(dialog.getByRole('button', { name: '确认删除' })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
