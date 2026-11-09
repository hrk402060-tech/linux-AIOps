import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosticJob, ExternalAlert } from '../../api/externalAlerts';
import { DiagnosticJobActionModal } from './DiagnosticJobActionModal';

const alert = { id: 'alert-1', title: 'CPU 高负载', asset_name: 'node-1' } as ExternalAlert;
const job = { id: 'job-1', status: 'running', display_status: 'running' } as DiagnosticJob;

function setup(kind: 'enqueue' | 'cancel' = 'enqueue', status = 202, pending?: Promise<Response>) {
  const target = kind === 'enqueue' ? { kind, alert } as const : { kind, alert, job } as const;
  const fetchMock = vi.fn(() => pending ?? Promise.resolve({ ok: status < 400, status, json: async () => status < 400 ? { ...job, display_status: 'cancelling' } : { detail: '服务器状态已改变' } }));
  vi.stubGlobal('fetch', fetchMock);
  const onClose = vi.fn();
  const onUpdated = vi.fn(async () => undefined);
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}><DiagnosticJobActionModal target={target} onClose={onClose} onUpdated={onUpdated} /></ConfigProvider>);
  return { ...view, fetchMock, onClose, onUpdated };
}

afterEach(() => vi.unstubAllGlobals());

describe('DiagnosticJobActionModal', () => {
  it.each(['enqueue', 'cancel'] as const)('requires human %s confirmation and preserves the machine target, trimming outer whitespace', async (kind) => {
    const { fetchMock } = setup(kind);
    const dialog = await screen.findByRole('dialog');
    const confirmation = within(dialog).getByRole('textbox', { name: '诊断确认文本' });
    const reason = within(dialog).getByRole('textbox', { name: '诊断操作原因' });
    const submit = within(dialog).getByRole('button', { name: kind === 'enqueue' ? '确认入队' : '确认取消' });
    const expected = kind === 'enqueue' ? '提交只读诊断' : '取消只读诊断';
    expect(within(dialog).getByText(expected)).toBeInTheDocument();
    expect(within(dialog).getByText('请填写 3～1000 个字符，首尾空格不计入；操作原因将保留在审计记录中。')).toBeInTheDocument();
    expect(submit).toBeDisabled();
    fireEvent.change(reason, { target: { value: '  人工处理  ' } });
    fireEvent.change(confirmation, { target: { value: 'wrong' } });
    expect(submit).toBeDisabled();
    fireEvent.change(confirmation, { target: { value: 'alert-1' } });
    expect(submit).toBeDisabled();
    fireEvent.change(confirmation, { target: { value: `  ${expected}  ` } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(kind === 'enqueue' ? '/api/v1/alerts/alert-1/diagnostics' : '/api/v1/alerts/alert-1/diagnostic-jobs/job-1/cancel', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ confirmation: 'alert-1', reason: '人工处理' }),
    })));
  });

  it.each(['enqueue', 'cancel'] as const)('requires a trimmed reason of 3 to 1000 characters for %s', async (kind) => {
    const { fetchMock } = setup(kind);
    const dialog = await screen.findByRole('dialog');
    const reason = within(dialog).getByRole('textbox', { name: '诊断操作原因' });
    const submit = within(dialog).getByRole('button', { name: kind === 'enqueue' ? '确认入队' : '确认取消' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断确认文本' }), {
      target: { value: kind === 'enqueue' ? '提交只读诊断' : '取消只读诊断' },
    });
    for (const value of ['   ', '  排查  ', '原'.repeat(1001)]) {
      fireEvent.change(reason, { target: { value } });
      expect(submit).toBeDisabled();
      fireEvent.click(submit);
      expect(fetchMock).not.toHaveBeenCalled();
    }
    fireEvent.change(reason, { target: { value: '  查原因  ' } });
    expect(submit).toBeEnabled();
    fireEvent.change(reason, { target: { value: '原'.repeat(1000) } });
    expect(submit).toBeEnabled();
  });

  it('guards duplicate submission synchronously and labels cancellation as requested only', async () => {
    let resolveRequest: (value: Response) => void = () => { throw new Error('Request missing'); };
    const pending = new Promise<Response>((resolve) => { resolveRequest = resolve; });
    const { fetchMock, onUpdated } = setup('cancel', 202, pending);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断确认文本' }), { target: { value: '取消只读诊断' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工停止采集' } });
    const submit = within(dialog).getByRole('button', { name: '确认取消' });
    fireEvent.click(submit); fireEvent.click(submit);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(submit).toBeDisabled();
    resolveRequest({ ok: true, status: 202, json: async () => ({ ...job, display_status: 'cancelling' }) } as Response);
    await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('取消请求已登记，等待当前只读连接结束')).toBeInTheDocument();
    expect(screen.queryByText('任务已取消')).not.toBeInTheDocument();
  });

  it('refreshes authoritative state after a conflict rather than resubmitting', async () => {
    const { onUpdated, onClose, fetchMock } = setup('enqueue', 409);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断确认文本' }), { target: { value: '提交只读诊断' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工重试诊断' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '确认入队' }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('服务器状态已改变')).toBeInTheDocument();
  });

  it('discards the previous target inputs when switching alerts', async () => {
    const { rerender } = setup();
    fireEvent.change(await screen.findByRole('textbox', { name: '诊断确认文本' }), { target: { value: '提交只读诊断' } });
    fireEvent.change(screen.getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工排查' } });
    rerender(<DiagnosticJobActionModal target={{ kind: 'enqueue', alert: { ...alert, id: 'alert-2', title: '内存告警' } }} onClose={vi.fn()} onUpdated={vi.fn(async () => undefined)} />);
    expect(screen.getByRole('textbox', { name: '诊断确认文本' })).toHaveValue('');
    expect(screen.getByRole('textbox', { name: '诊断操作原因' })).toHaveValue('');
    expect(screen.getByRole('button', { name: '确认入队' })).toBeDisabled();
  });

  it('clears inputs when the alert changes even if the cancellation job ID is unchanged', async () => {
    const { rerender } = setup('cancel');
    fireEvent.change(await screen.findByRole('textbox', { name: '诊断确认文本' }), { target: { value: '取消只读诊断' } });
    fireEvent.change(screen.getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工停止采集' } });
    rerender(<DiagnosticJobActionModal target={{ kind: 'cancel', alert: { ...alert, id: 'alert-2' }, job }} onClose={vi.fn()} onUpdated={vi.fn(async () => undefined)} />);
    expect(screen.getByRole('textbox', { name: '诊断确认文本' })).toHaveValue('');
    expect(screen.getByRole('textbox', { name: '诊断操作原因' })).toHaveValue('');
    expect(screen.getByRole('button', { name: '确认取消' })).toBeDisabled();
  });

  it('clears inputs after closing and reopening the same target', async () => {
    const { rerender, onClose, onUpdated } = setup();
    fireEvent.change(await screen.findByRole('textbox', { name: '诊断确认文本' }), { target: { value: '提交只读诊断' } });
    fireEvent.change(screen.getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工排查' } });
    rerender(<DiagnosticJobActionModal target={null} onClose={onClose} onUpdated={onUpdated} />);
    rerender(<DiagnosticJobActionModal target={{ kind: 'enqueue', alert }} onClose={onClose} onUpdated={onUpdated} />);
    expect(await screen.findByRole('textbox', { name: '诊断确认文本' })).toHaveValue('');
    expect(screen.getByRole('textbox', { name: '诊断操作原因' })).toHaveValue('');
    expect(screen.getByRole('button', { name: '确认入队' })).toBeDisabled();
  });

  it('does not close a new target when a previous request resolves', async () => {
    let resolveRequest: (value: Response) => void = () => { throw new Error('Request missing'); };
    const pending = new Promise<Response>((resolve) => { resolveRequest = resolve; });
    const { rerender, onClose, onUpdated, fetchMock } = setup('enqueue', 202, pending);
    fireEvent.change(await screen.findByRole('textbox', { name: '诊断确认文本' }), { target: { value: '提交只读诊断' } });
    fireEvent.change(screen.getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '人工排查' } });
    fireEvent.click(screen.getByRole('button', { name: '确认入队' }));
    rerender(<DiagnosticJobActionModal target={{ kind: 'enqueue', alert: { ...alert, id: 'alert-2' } }} onClose={onClose} onUpdated={onUpdated} />);
    resolveRequest({ ok: true, status: 202, json: async () => job } as Response);
    await waitFor(() => expect(screen.getByRole('textbox', { name: '诊断确认文本' })).toBeEnabled());
    expect(onClose).not.toHaveBeenCalled();
    expect(onUpdated).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('textbox', { name: '诊断确认文本' })).toHaveValue('');
  });
});
