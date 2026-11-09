import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ExternalAlert } from '../../api/externalAlerts';
import type { RecoveryHistory, RecoveryVerification } from '../../api/recovery';
import { RecoveryVerificationPanel } from './RecoveryVerificationPanel';

const alert = {
  id: 'recurring-alert', asset_id: 'asset-1', status: 'in_progress',
  received_at: '2026-11-06T01:00:00Z',
} as ExternalAlert;
const recurrence = { ...alert, received_at: '2026-11-06T02:00:00Z' };
const record: RecoveryVerification = {
  id: 'old-verification', alert_id: alert.id, asset_id: alert.asset_id,
  kind: 'monitor', metric: 'availability', threshold: null, probe_id: null,
  observation_minutes: 5, max_gap_seconds: 120, status: 'observing', version: 1,
  operator: 'operator', reason: '上一轮观察', started_at: '2026-11-06T01:01:00Z',
  deadline_at: '2026-11-06T01:06:00Z', finished_at: null, last_evaluation: null,
};
const base: RecoveryHistory = { total: 0, items: [], options: {
  asset_enabled: true, active_verification: false,
  metrics: [{ value: 'availability', label: '在线状态' }], probes: [],
  collection_mode: 'existing_evidence_only',
} };
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const createFetch = () => vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response(base));
const currentUser = { role: 'operator' as const };
const view = (item: ExternalAlert, onUpdated: () => void) => (
  <ConfigProvider theme={{ token: { motion: false } }}>
    <RecoveryVerificationPanel alert={item} currentUser={currentUser} onUpdated={onUpdated} />
  </ConfigProvider>
);
function deferred() {
  let resolve: (value: ReturnType<typeof response>) => void = () => {
    throw new Error('Deferred response has not been initialized');
  };
  const promise = new Promise<ReturnType<typeof response>>((done) => { resolve = done; });
  return { promise, resolve };
}
async function openConfirmedCreation() {
  fireEvent.click(await screen.findByRole('button', { name: '发起恢复验证' }));
  const dialog = within(await screen.findByRole('dialog'));
  fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工处理后核验' } });
  fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '发起恢复验证' } });
  return dialog.getByRole('button', { name: '确认发起' });
}
afterEach(() => vi.unstubAllGlobals());

describe('恢复验证告警轮次隔离', () => {
  it('keeps historical observations readable without allowing actions or blocking a new verification', async () => {
    const fetchMock = createFetch().mockResolvedValue(response({
      ...base, total: 1, items: [{ ...record, is_current: false }],
    }));
    vi.stubGlobal('fetch', fetchMock);
    render(view(recurrence, vi.fn()));
    expect(await screen.findByText('历史轮次')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '核验当前证据' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消验证' })).not.toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(response({ total: 0, items: [] }));
    fireEvent.click(screen.getByRole('button', { name: '查看评估记录' }));
    expect(await screen.findByText('恢复评估记录')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/old-verification/evaluations'), expect.objectContaining({ credentials: 'include' }),
    ));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('clears confirmation and waits for authoritative history when the same alert recurs', async () => {
    const next = deferred();
    const fetchMock = createFetch().mockResolvedValueOnce(response(base))
      .mockReturnValueOnce(next.promise).mockResolvedValue(response(base));
    vi.stubGlobal('fetch', fetchMock);
    const onUpdated = vi.fn();
    const { rerender } = render(view(alert, onUpdated));
    expect(await openConfirmedCreation()).toBeEnabled();
    rerender(view(recurrence, onUpdated));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeDisabled();
    await act(async () => { next.resolve(response(base)); });
    fireEvent.click(screen.getByRole('button', { name: '发起恢复验证' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByLabelText('验证原因')).toHaveValue('');
    expect(dialog.getByLabelText('恢复验证确认文本')).toHaveValue('');
    expect(dialog.getByRole('button', { name: '确认发起' })).toBeDisabled();
    expect(onUpdated).not.toHaveBeenCalled();
  });

  it('ignores a late previous-cycle history response after recurrence', async () => {
    const old = deferred();
    const fetchMock = createFetch().mockReturnValueOnce(old.promise).mockResolvedValue(response(base));
    vi.stubGlobal('fetch', fetchMock);
    const { rerender } = render(view(alert, vi.fn()));
    rerender(view(recurrence, vi.fn()));
    await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
    await act(async () => {
      old.resolve(response({ ...base, total: 1, items: [record] }));
    });
    expect(screen.queryByText('上一轮观察')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled();
  });

  it('clears an open evaluation drawer and ignores its late response after recurrence', async () => {
    const oldEvaluation = deferred();
    const fetchMock = createFetch().mockResolvedValueOnce(response({
      ...base, total: 1, items: [record],
    }));
    vi.stubGlobal('fetch', fetchMock);
    const { rerender } = render(view(alert, vi.fn()));
    const historyButton = await screen.findByRole('button', { name: '查看评估记录' });
    fetchMock.mockReturnValueOnce(oldEvaluation.promise);
    fireEvent.click(historyButton);
    expect(await screen.findByText('恢复评估记录')).toBeInTheDocument();
    rerender(view(recurrence, vi.fn()));
    await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
    await act(async () => {
      oldEvaluation.resolve(response({ total: 1, items: [{
        id: 'old-evaluation', verification_id: record.id, outcome: 'verified',
        reason_code: 'check_passed', summary: '上一轮检查结论',
        operator: 'operator', trace_id: 'old-trace',
        evaluated_at: record.deadline_at, evidence: [],
      }] }));
    });
    expect(screen.queryByText('恢复评估记录')).not.toBeInTheDocument();
    expect(screen.queryByText('上一轮检查结论')).not.toBeInTheDocument();
  });

  it('ignores an earlier human request result after recurrence without resubmitting it', async () => {
    const oldWrite = deferred();
    const fetchMock = createFetch();
    vi.stubGlobal('fetch', fetchMock);
    const onUpdated = vi.fn();
    const { rerender } = render(view(alert, onUpdated));
    const confirm = await openConfirmedCreation();
    fetchMock.mockReturnValueOnce(oldWrite.promise);
    fireEvent.click(confirm);
    rerender(view(recurrence, onUpdated));
    await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
    await act(async () => { oldWrite.resolve(response(record)); });
    expect(onUpdated).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });
});
