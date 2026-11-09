import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getAlertConnectorReceipts, type AlertConnector, type ConnectorReceipt, type ConnectorReceiptPage,
} from '../../api/externalAlerts';
import { AlertConnectorReceiptsModal } from './AlertConnectorReceiptsModal';

vi.mock('../../api/externalAlerts', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../api/externalAlerts')>(), getAlertConnectorReceipts: vi.fn(),
}));

const connector: AlertConnector = {
  id: 'connector-1', name: '生产告警', source_type: 'prometheus', status: 'active',
  secret_masked: '********', operator: 'admin', created_at: '2026-11-06T00:00:00Z',
  updated_at: '2026-11-06T00:00:00Z', rotated_at: null,
};
const receipt: ConnectorReceipt = {
  id: 'receipt-1', connector_id: connector.id, received_at: '2026-11-06T01:00:00Z',
  http_status: 200, result: 'success', outcome: 'created',
  error_code: null, message: 'Traceback secret=do-not-show raw_body signature',
  alert_id: 'alert-1',
};

function page(items: ConnectorReceipt[] = [receipt], total = items.length, offset = 0): ConnectorReceiptPage {
  return { items, total, offset, limit: 20 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function view(target: AlertConnector | null = connector, enabled = true) {
  return <ConfigProvider theme={{ token: { motion: false } }}>
    <AlertConnectorReceiptsModal target={target} enabled={enabled} onClose={vi.fn()} />
  </ConfigProvider>;
}

beforeEach((): void => {
  vi.mocked(getAlertConnectorReceipts).mockResolvedValue(page());
});
afterEach(() => vi.resetAllMocks());

describe('AlertConnectorReceiptsModal', () => {
  it('shows loading and fixed safe receipt outcomes without the request or raw exception', async () => {
    const pending = deferred<ConnectorReceiptPage>();
    vi.mocked(getAlertConnectorReceipts).mockReturnValue(pending.promise);
    render(view());
    expect(screen.getByText('正在加载接收记录')).toBeInTheDocument();
    expect(screen.queryByText('暂无接收记录')).toBeNull();
    await act(async () => { pending.resolve(page()); });
    expect(await screen.findByText('已创建告警')).toBeInTheDocument();
    expect(screen.getByText('HTTP 200')).toBeInTheDocument();
    expect(screen.queryByText(/Traceback|do-not-show|raw_body|signature/)).toBeNull();
  });

  it('requests twenty-record pages and resets to page one when filtering', async () => {
    const second = { ...receipt, id: 'receipt-21', outcome: 'duplicate' as const };
    vi.mocked(getAlertConnectorReceipts).mockResolvedValueOnce(page([receipt], 21))
      .mockResolvedValueOnce(page([second], 21, 20))
      .mockResolvedValueOnce(page([{ ...receipt, result: 'failure', outcome: 'rejected', http_status: 401 }]))
      .mockResolvedValueOnce(page());
    render(view());
    await screen.findByText('已创建告警');
    expect(getAlertConnectorReceipts).toHaveBeenCalledWith(connector.id, 0, 20, undefined);
    fireEvent.click(screen.getByRole('button', { name: '下一页接收记录' }));
    expect(await screen.findByText('重复告警已接收')).toBeInTheDocument();
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith(connector.id, 20, 20, undefined);
    expect(screen.getByText('第 2 / 2 页 · 共 21 条')).toBeInTheDocument();
    fireEvent.click(screen.getByText('失败', { selector: '.ant-segmented-item-label' }));
    await screen.findByText('接收被拒绝');
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith(connector.id, 0, 20, 'failure');
    expect(screen.getByRole('button', { name: '上一页接收记录' })).toBeDisabled();
    fireEvent.click(screen.getByText('成功', { selector: '.ant-segmented-item-label' }));
    await screen.findByText('已创建告警');
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith(connector.id, 0, 20, 'success');
  });

  it('clears stale rows on refresh failure and offers manual retry with a fixed error', async () => {
    vi.mocked(getAlertConnectorReceipts).mockResolvedValueOnce(page())
      .mockRejectedValueOnce(new Error('Traceback password=do-not-show'))
      .mockResolvedValueOnce(page([]));
    render(view());
    await screen.findByText('已创建告警');
    fireEvent.click(screen.getByRole('button', { name: '刷新接收记录' }));
    expect(await screen.findByText('接收记录加载失败，请刷新后重试')).toBeInTheDocument();
    expect(screen.queryByText('已创建告警')).toBeNull();
    expect(screen.queryByText('暂无接收记录')).toBeNull();
    expect(screen.queryByText(/password|Traceback/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '刷新接收记录' }));
    expect(await screen.findByText('暂无接收记录')).toBeInTheDocument();
  });

  it.each([
    ['payload_too_large', '请求超过64KiB'],
    ['__proto__', '接收失败'],
  ])('shows the fixed failure reason for %s instead of server messages', async (code, label) => {
    vi.mocked(getAlertConnectorReceipts).mockResolvedValue(page([{
      ...receipt, result: 'failure', outcome: 'rejected', http_status: 400, error_code: code,
    }]));
    render(view());
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByText(/Traceback|do-not-show|raw_body|signature|__proto__/)).toBeNull();
  });

  it('preserves the current page on refresh', async () => {
    vi.mocked(getAlertConnectorReceipts).mockResolvedValueOnce(page([receipt], 21))
      .mockResolvedValue(page([{ ...receipt, outcome: 'reopened' }], 21, 20));
    render(view());
    await screen.findByText('已创建告警');
    fireEvent.click(screen.getByRole('button', { name: '下一页接收记录' }));
    await screen.findByText('已重新打开告警');
    fireEvent.click(screen.getByRole('button', { name: '刷新接收记录' }));
    await waitFor(() => expect(getAlertConnectorReceipts).toHaveBeenCalledTimes(3));
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith(connector.id, 20, 20, undefined);
  });

  it('discards the previous connector response when switching targets', async () => {
    const late = deferred<ConnectorReceiptPage>();
    vi.mocked(getAlertConnectorReceipts).mockReturnValueOnce(late.promise).mockResolvedValueOnce(page([]));
    const { rerender } = render(view());
    await waitFor(() => expect(getAlertConnectorReceipts).toHaveBeenCalledTimes(1));
    rerender(view({ ...connector, id: 'connector-2', name: '备用告警' }));
    await screen.findByText('暂无接收记录');
    await act(async () => { late.resolve(page()); });
    expect(screen.queryByText('已创建告警')).toBeNull();
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith('connector-2', 0, 20, undefined);
  });

  it('ignores an old all-results response after switching the filter', async () => {
    const late = deferred<ConnectorReceiptPage>();
    vi.mocked(getAlertConnectorReceipts).mockReturnValueOnce(late.promise).mockResolvedValueOnce(page([]));
    render(view());
    await waitFor(() => expect(getAlertConnectorReceipts).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('失败', { selector: '.ant-segmented-item-label' }));
    await screen.findByText('暂无接收记录');
    await act(async () => { late.resolve(page()); });
    expect(screen.queryByText('已创建告警')).toBeNull();
    expect(getAlertConnectorReceipts).toHaveBeenLastCalledWith(connector.id, 0, 20, 'failure');
  });

  it('rejects a response for another connector instead of displaying its data', async () => {
    vi.mocked(getAlertConnectorReceipts).mockResolvedValue(page([{ ...receipt, connector_id: 'other-connector' }]));
    render(view());
    expect(await screen.findByText('接收记录加载失败，请刷新后重试')).toBeInTheDocument();
    expect(screen.queryByText('已创建告警')).toBeNull();
  });

  it('never fetches when disabled and clears a closed pending context', async () => {
    const late = deferred<ConnectorReceiptPage>();
    const { rerender } = render(view(connector, false));
    expect(getAlertConnectorReceipts).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
    vi.mocked(getAlertConnectorReceipts).mockReturnValueOnce(late.promise);
    rerender(view());
    await waitFor(() => expect(getAlertConnectorReceipts).toHaveBeenCalledTimes(1));
    rerender(view(null));
    await act(async () => { late.resolve(page()); });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText('已创建告警')).toBeNull();
  });
});
