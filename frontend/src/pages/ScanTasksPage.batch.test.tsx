import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ScanTask } from '../types/scan-task';
import { ScanTasksPage } from './ScanTasksPage';

const base = { status: 'completed', scan_type: 'comprehensive', progress: 100, read_only: true,
  asset_ids: [], asset_snapshot: [], knowledge_version: null, archived: false,
  execution_log: [], findings: [], created_at: '2026-10-10T00:00:00Z' } as unknown as ScanTask;
function setup(count = 12, role: 'admin' | 'operator' | 'auditor' = 'admin') {
  const tasks = Array.from({ length: count }, (_, index) => ({ ...base, id: 'task-' + index, name: `任务 ${index}` }));
  const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => reply(String(input).startsWith('/api/v1/scan-tasks') ? tasks : []));
  vi.stubGlobal('fetch', fetchMock);
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}><ScanTasksPage currentUser={{ role }} /></ConfigProvider>);
  return { ...view, fetchMock, tasks };
}
afterEach(() => vi.unstubAllGlobals());

describe('Scan task selection', () => {
  it('selects the current page, explicitly selects all matching tasks and clears on a new filter', async () => {
    setup(); await screen.findByText('任务 0');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all|全选/ }));
    expect(screen.getByText('已选 10 项')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全选筛选结果' }));
    expect(screen.getByText('已选 12 项')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('搜索任务或目标资产'), { target: { value: '任务 0' } });
    expect(screen.queryByText('已选 0 项')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '批量归档' })).not.toBeInTheDocument();
  });
  it('bounds whole-filter selection at 100 rather than silently taking a subset', async () => {
    setup(101); await screen.findByText('任务 0');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all|全选/ }));
    expect(screen.getByRole('button', { name: '全选筛选结果' })).toBeDisabled();
    expect(screen.getByText('已选 10 项')).toBeInTheDocument();
  });
  it('keeps auditor access read-only including row operations and selection', async () => {
    setup(1, 'auditor'); await screen.findByText('任务 0');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建任务' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /批量|归档|重跑/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 任务 0' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '下载 任务 0' })).toBeEnabled();
  });
  it('archives selected tasks only after confirmation and refreshes authoritative data', async () => {
    const { fetchMock, tasks } = setup(2, 'operator'); await screen.findByText('任务 0');
    let active = tasks;
    fetchMock.mockImplementation(async (input) => ({ ok: true, status: 200, json: async () => {
      if (String(input).endsWith('/archive')) { active = active.filter((row) => !String(input).includes('/' + row.id + '/')); return tasks[0]; }
      return String(input).startsWith('/api/v1/scan-tasks') ? active : [];
    } }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all|全选/ }));
    fireEvent.click(screen.getByRole('button', { name: '批量归档' }));
    expect(fetchMock.mock.calls).toHaveLength(2);
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认归档' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '批量归档' })).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/archive'))).toHaveLength(2);
    expect(await screen.findByText('成功 2 项，失败或未执行 0 项')).toBeInTheDocument();
  });
  it('keeps the unselected page compact and shows deletion only to administrators', async () => {
    const { container } = setup(2); await screen.findByText('任务 0');
    expect(container.querySelector('.metric-band')).not.toBeInTheDocument();
    expect(screen.getByLabelText('任务统计')).toHaveTextContent('共 2 项');
    expect(screen.queryByRole('button', { name: /批量|全选筛选结果|清空选择/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '删除 任务 0' })).toBeEnabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all|全选/ }));
    expect(screen.getByRole('button', { name: '批量删除' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
    expect(screen.queryByRole('button', { name: '批量删除' })).not.toBeInTheDocument();
  });
  it('does not offer deletion to operators', async () => {
    setup(2, 'operator'); await screen.findByText('任务 0');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all|全选/ }));
    expect(screen.queryByRole('button', { name: /删除/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量归档' })).toBeEnabled();
  });
  it('deletes one completed task only after confirmation and refreshes the list', async () => {
    const { fetchMock, tasks } = setup(2); await screen.findByText('任务 0');
    let active = tasks;
    fetchMock.mockImplementation(async (input, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        active = active.filter((item) => !String(input).endsWith('/' + item.id));
        return { ok: true, status: 204, json: async () => { throw new Error('No JSON body'); } };
      }
      return { ok: true, status: 200, json: async () => String(input).startsWith('/api/v1/scan-tasks') ? active : [] };
    });
    fireEvent.click(screen.getByRole('button', { name: '删除 任务 0' }));
    const dialog = within(await screen.findByRole('dialog', { name: '删除扫描任务' }));
    fireEvent.change(dialog.getByLabelText('删除原因'), { target: { value: '任务已完成' } });
    fireEvent.change(dialog.getByLabelText('确认文本'), { target: { value: '删除 1 项任务' } });
    fireEvent.click(dialog.getByRole('button', { name: '确认删除' }));
    await waitFor(() => expect(screen.queryByText('任务 0')).not.toBeInTheDocument());
    expect(screen.getByText('任务 1')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/task-0', expect.objectContaining({ method: 'DELETE' }));
    expect(screen.getByText('已删除 1 项，失败或未执行 0 项')).toBeInTheDocument();
  });
});
