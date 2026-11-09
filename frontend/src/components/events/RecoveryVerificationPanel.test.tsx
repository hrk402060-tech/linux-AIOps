import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ExternalAlert } from '../../api/externalAlerts';
import type { RecoveryHistory, RecoveryVerification } from '../../api/recovery';
import { RecoveryVerificationPanel } from './RecoveryVerificationPanel';

const alert = { id: 'alert-recovery', asset_id: 'asset-recovery', status: 'in_progress' } as ExternalAlert;
const record: RecoveryVerification = { id: 'verification-1', alert_id: alert.id, asset_id: alert.asset_id,
  kind: 'monitor', metric: 'availability', threshold: null, probe_id: null,
  observation_minutes: 5, max_gap_seconds: 120, status: 'observing', version: 1,
  operator: 'operator', reason: '人工已处理', started_at: '2026-10-10T00:00:00Z',
  deadline_at: '2026-10-10T00:05:00Z', finished_at: null, last_evaluation: null };
const base: RecoveryHistory = { total: 0, items: [], options: { asset_enabled: true,
  metrics: [{ value: 'availability', label: '在线状态' }, { value: 'cpu_percent', label: 'CPU 使用率' }],
  probes: [], collection_mode: 'existing_evidence_only' } };
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
function setup(role: 'admin' | 'operator' | 'auditor' = 'operator', data = base) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => response(data)); vi.stubGlobal('fetch', fetchMock);
  const onUpdated = vi.fn();
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <RecoveryVerificationPanel alert={alert} currentUser={{ role }} onUpdated={onUpdated} />
  </ConfigProvider>);
  return { fetchMock, onUpdated, ...view };
}
afterEach(() => vi.unstubAllGlobals());

describe('RecoveryVerificationPanel', () => {
  it('blocks creation when another page contains the active verification', async () => {
    setup('operator', { ...base, options: { ...base.options, active_verification: true } });
    expect(await screen.findByRole('button', { name: '发起恢复验证' })).toBeDisabled();
  });
  it('discards late history when switching to a different keyed alert', async () => {
    let resolve: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    const fetchMock = vi.fn().mockImplementationOnce(() => new Promise((done) => { resolve = done; }))
      .mockResolvedValue(response(base));
    vi.stubGlobal('fetch', fetchMock);
    const { rerender } = render(<RecoveryVerificationPanel key={alert.id} alert={alert} currentUser={{ role: 'operator' }} onUpdated={() => undefined} />);
    rerender(<RecoveryVerificationPanel key="another" alert={{ ...alert, id: 'another' }} currentUser={{ role: 'operator' }} onUpdated={() => undefined} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
    await act(async () => { resolve(response({ ...base, total: 1, items: [record] })); });
    expect(screen.queryByText('待观察')).not.toBeInTheDocument();
  });
  it('refreshes after 403 and never retries the write after a network-ambiguous response', async () => {
    const { fetchMock } = setup('operator', { ...base, total: 1, items: [record] });
    const fill = async () => {
      fireEvent.click(await screen.findByRole('button', { name: '核验当前证据' }));
      const dialog = within(await screen.findByRole('dialog'));
      fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工核验' } });
      fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '核验恢复证据' } });
      return dialog.getByRole('button', { name: '确认核验' });
    };
    let confirm = await fill();
    fetchMock.mockResolvedValueOnce(response({ detail: '权限失效' }, 403));
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    confirm = await fill();
    fetchMock.mockRejectedValueOnce(new TypeError('network-ambiguous'));
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '核验当前证据' })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2);
  });
  it('requires exact visible confirmation and trimmed reason before creating a verification', async () => {
    const { fetchMock, onUpdated } = setup();
    fireEvent.click(await screen.findByRole('button', { name: '发起恢复验证' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('发起恢复验证', { selector: 'code' })).toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工已修复' } });
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: 'wrong' } });
    expect(dialog.getByRole('button', { name: '确认发起' })).toBeDisabled();
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: ' 发起恢复验证 ' } });
    expect(dialog.getByRole('button', { name: '确认发起' })).toBeEnabled();
    fetchMock.mockResolvedValueOnce(response(record)).mockResolvedValue(response({ ...base, total: 1, items: [record] }));
    fireEvent.click(dialog.getByRole('button', { name: '确认发起' }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(post?.[1]?.body as string)).toMatchObject({ confirmation: alert.id, reason: '人工已修复', kind: 'monitor', metric: 'availability' });
  });
  it('keeps auditors read-only while showing verification evidence and history', async () => {
    setup('auditor', { ...base, total: 1, items: [{ ...record, status: 'verified' }] });
    expect(await screen.findByText('检查通过')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /核验|取消验证/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看评估记录' })).toBeEnabled();
  });
  it('evaluates with version and human confirmation, without duplicate submission', async () => {
    const { fetchMock } = setup('admin', { ...base, total: 1, items: [record] });
    fireEvent.click(await screen.findByRole('button', { name: '核验当前证据' }));
    const dialog = within(await screen.findByRole('dialog'));
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工复核' } });
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '核验恢复证据' } });
    let resolve: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing pending request'); };
    fetchMock.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const button = dialog.getByRole('button', { name: '确认核验' });
    fireEvent.click(button); fireEvent.click(button);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/verification-1/evaluate'), expect.objectContaining({
      body: JSON.stringify({ expected_version: 1, confirmation: alert.id, reason: '人工复核' }),
    }));
    await act(async () => { resolve(response(record)); });
  });
  it('refreshes authoritative history on a 409 instead of retrying the write', async () => {
    const { fetchMock } = setup('operator', { ...base, total: 1, items: [record] });
    fireEvent.click(await screen.findByRole('button', { name: '取消验证' }));
    const dialog = within(await screen.findByRole('dialog'));
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工取消' } });
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '取消恢复验证' } });
    fetchMock.mockResolvedValueOnce(response({ detail: '验证版本变化' }, 409))
      .mockResolvedValue(response({ ...base, total: 1, items: [{ ...record, status: 'cancelled' }] }));
    fireEvent.click(dialog.getByRole('button', { name: '确认取消' }));
    expect(await screen.findByText('已取消')).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });
  it('offers explicit retry after history loading fails, without enabling creation', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValue(response(base));
    vi.stubGlobal('fetch', fetchMock);
    render(<RecoveryVerificationPanel alert={alert} currentUser={{ role: 'operator' }} onUpdated={() => undefined} />);
    expect(await screen.findByText('network')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '刷新恢复验证' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
  });

  it.each([
    ['发起恢复验证', '发起恢复验证', '确认发起'],
    ['核验当前证据', '核验恢复证据', '确认核验'],
    ['取消验证', '取消恢复验证', '确认取消'],
  ])('requires exact Chinese confirmation and a bounded trimmed reason for %s', async (action, phrase, button) => {
    const { fetchMock } = setup('operator', action === '发起恢复验证' ? base : { ...base, total: 1, items: [record] });
    fireEvent.click(await screen.findByRole('button', { name: action }));
    const dialog = within(await screen.findByRole('dialog'));
    const confirm = dialog.getByLabelText('恢复验证确认文本');
    const reason = dialog.getByLabelText('验证原因');
    const submit = dialog.getByRole('button', { name: button });
    expect(dialog.getByText(phrase, { selector: 'code' })).toBeInTheDocument();
    expect(dialog.getByText('请填写 3～1000 个字符，首尾空格不计入；操作原因将保留在审计记录中。')).toBeInTheDocument();
    fireEvent.change(reason, { target: { value: '  人工核验  ' } });
    for (const value of ['wrong', alert.id]) {
      fireEvent.change(confirm, { target: { value } });
      expect(submit).toBeDisabled();
    }
    fireEvent.change(confirm, { target: { value: `  ${phrase}  ` } });
    expect(submit).toBeEnabled();
    for (const value of ['  ', '  核验  ', '原'.repeat(1001)]) {
      fireEvent.change(reason, { target: { value } });
      expect(submit).toBeDisabled();
      fireEvent.click(submit);
      expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    }
    fireEvent.change(reason, { target: { value: '  人工核验  ' } });
    fetchMock.mockResolvedValueOnce(response(record));
    fireEvent.click(submit);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(post?.[1]?.body as string)).toMatchObject({ confirmation: alert.id, reason: '人工核验',
      ...(action === '发起恢复验证' ? { observation_minutes: 15, max_gap_seconds: 360 } : { expected_version: 1 }) });
  });

  it('resets all creation drafts on close and reopen', async () => {
    setup();
    fireEvent.click(await screen.findByRole('button', { name: '发起恢复验证' }));
    let dialog = within(await screen.findByRole('dialog'));
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工核验' } });
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '发起恢复验证' } });
    fireEvent.change(dialog.getByLabelText('观察窗口'), { target: { value: '20' } });
    fireEvent.change(dialog.getByLabelText('最大采样间隔'), { target: { value: '500' } });
    fireEvent.click(dialog.getByRole('button', { name: /返\s*回/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '发起恢复验证' }));
    dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByLabelText('验证原因')).toHaveValue('');
    expect(dialog.getByLabelText('恢复验证确认文本')).toHaveValue('');
    expect(dialog.getByLabelText('观察窗口')).toHaveValue('15');
    expect(dialog.getByLabelText('最大采样间隔')).toHaveValue('360');
    expect(dialog.getByRole('button', { name: '确认发起' })).toBeDisabled();
  });

  it.each(['alert', 'role'] as const)('discards an old write response after the %s changes', async (change) => {
    const { fetchMock, rerender, onUpdated } = setup('operator', { ...base, total: 1, items: [record] });
    fireEvent.click(await screen.findByRole('button', { name: '核验当前证据' }));
    const dialog = within(await screen.findByRole('dialog'));
    fireEvent.change(dialog.getByLabelText('验证原因'), { target: { value: '人工核验' } });
    fireEvent.change(dialog.getByLabelText('恢复验证确认文本'), { target: { value: '核验恢复证据' } });
    let resolve: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing pending request'); };
    fetchMock.mockReturnValueOnce(new Promise((done) => { resolve = done; })).mockResolvedValue(response(base));
    fireEvent.click(dialog.getByRole('button', { name: '确认核验' }));
    rerender(<RecoveryVerificationPanel alert={change === 'alert' ? { ...alert, id: 'alert-another' } : alert}
      currentUser={{ role: change === 'role' ? 'auditor' : 'operator' }} onUpdated={onUpdated} />);
    await act(async () => { resolve(response(record)); });
    expect(onUpdated).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('待观察')).not.toBeInTheDocument();
    if (change === 'role') expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
  });

  it('shows bounded Chinese evidence fields instead of raw JSON, and resolves probe names', async () => {
    const probe = { ...record, kind: 'probe' as const, metric: null, probe_id: 'probe-1' };
    const { fetchMock } = setup('auditor', { ...base, total: 1, items: [probe],
      options: { ...base.options, probes: [{ id: 'probe-1', name: '订单服务健康检查' }] } });
    expect(await screen.findByText('订单服务健康检查')).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(response({ total: 1, items: [{
      id: 'evaluation-1', outcome: 'verified', evaluated_at: record.deadline_at, operator: 'operator',
      summary: '所选检查通过，不代表整体恢复', trace_id: 'trace-technical',
      evidence: [{ source: 'monitoring_snapshot', id: 'snapshot-technical', at: record.started_at,
        status: 'online', metric: 'cpu_percent', value: 30, threshold: 80, unexpected: 'must-not-render' },
      { source: 'http_probe_run', id: 'probe-run-technical', at: record.started_at, status: 'healthy', http_status: 200,
        latency_ms: 42, trace_id: 'trace-probe' },
      { source: 'future_source', at: 'invalid', status: 'future_status', metric: 'future_metric', value: { unsafe: 'no' } }],
    }] }));
    fireEvent.click(screen.getByRole('button', { name: '查看评估记录' }));
    expect(await screen.findByText('监控采样')).toBeInTheDocument();
    expect(screen.getByText('API 拨测记录')).toBeInTheDocument();
    expect(screen.getByText('在线')).toBeInTheDocument();
    expect(screen.getByText('健康')).toBeInTheDocument();
    expect(screen.getByText('CPU 使用率')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
    expect(screen.getByText('80')).toBeInTheDocument();
    expect(screen.getByText('200')).toBeInTheDocument();
    expect(screen.getByText('42 毫秒')).toBeInTheDocument();
    expect(screen.getByText('未知来源')).toBeInTheDocument();
    expect(screen.getByText('未知状态')).toBeInTheDocument();
    expect(screen.getByText('未知指标')).toBeInTheDocument();
    expect(screen.getByText('时间无效')).toBeInTheDocument();
    expect(screen.queryByText(/must-not-render|"source":|"unsafe":/)).not.toBeInTheDocument();
    expect(screen.getAllByText('技术详情').length).toBeGreaterThan(0);
  });

  it('shows safe failure reasons and accepts historical evidence without new metadata', async () => {
    const { fetchMock } = setup('auditor', { ...base, total: 1, items: [record] });
    await screen.findByRole('button', { name: '查看评估记录' });
    fetchMock.mockResolvedValueOnce(response({ total: 1, items: [{
      id: 'evaluation-failed', outcome: 'failed', evaluated_at: record.deadline_at, operator: 'operator',
      summary: '观察到异常样本', trace_id: 'trace-evaluation',
      evidence: [
        { source: 'monitoring_snapshot', id: 'legacy', at: record.started_at, status: 'online' },
        { source: 'monitoring_snapshot', id: 'offline', at: record.started_at, status: 'offline',
          error_message: 'password=secret https://reader:secret@example.invalid/' },
        { source: 'http_probe_run', id: 'probe-error', at: record.started_at, status: 'error',
          error_code: 'connection_error', latency_ms: 0, trace_id: '9a014389-b3ee-4491-a1c5-f529654e9305' },
        { source: 'http_probe_run', id: 'probe-unknown', at: record.started_at, status: 'error',
          error_code: 'unrecognized-private-error', latency_ms: null },
      ],
    }] }));
    fireEvent.click(screen.getByRole('button', { name: '查看评估记录' }));
    expect(await screen.findByText('连接失败')).toBeInTheDocument();
    expect(screen.getByText('监控采集异常，原始错误请在受控日志中核查。')).toBeInTheDocument();
    expect(screen.getByText('0 毫秒')).toBeInTheDocument();
    expect(screen.getByText('9a014389-b3ee-4491-a1c5-f529654e9305')).toBeInTheDocument();
    expect(screen.getByText('未知错误')).toBeInTheDocument();
    expect(screen.getByText('在线')).toBeInTheDocument();
    expect(screen.getByText('监控采集异常，原始错误请在受控日志中核查。')).toBeInTheDocument();
    expect(screen.queryByText(/unrecognized-private-error|connection_error|password=secret|未提供 毫秒/)).not.toBeInTheDocument();
  });
});
