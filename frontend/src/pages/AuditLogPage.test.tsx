import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { AuditLogPage } from './AuditLogPage';

afterEach(() => vi.unstubAllGlobals());
it('shows and filters administrator notification audits using their backend source', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 1, items: [{ id: 'notification:1', source: 'alert_notification', action: 'notification_acknowledged', operator: 'ops-admin', detail: 'human acknowledgment', target: 'notice-1', occurred_at: '2026-10-03T01:00:00Z' }] }) });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuditLogPage /></ConfigProvider>);
  expect(await screen.findByText('告警通知')).toBeInTheDocument();
  expect(screen.queryByText('alert_notification')).not.toBeInTheDocument();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '审计来源' }));
  fireEvent.click(await screen.findByText('告警通知', { selector: '.ant-select-item-option-content' }));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    '/api/v1/audit-log?source=alert_notification&page=1&page_size=20', expect.anything(),
  ));
});
it('filters provider configuration audits using the exact backend source', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 0, items: [] }) });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><AuditLogPage /></ConfigProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '审计来源' }));
  fireEvent.click(await screen.findByText('AI 模型配置'));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(
    '/api/v1/audit-log?source=ai_provider_config&page=1&page_size=20', expect.anything(),
  ));
});
it('shows normalized audit metadata without terminal content', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 1, items: [{ id: 'asset:1', source: 'asset_lifecycle', action: 'disabled', operator: 'security-admin', detail: 'planned maintenance', target: 'node-1', occurred_at: '2026-09-09T00:00:00Z' }] }) }));
  render(<ConfigProvider><AuditLogPage /></ConfigProvider>);
  expect(await screen.findByText('security-admin')).toBeInTheDocument();
  expect(screen.getByText('资产生命周期')).toBeInTheDocument();
  expect(screen.queryByText('terminal input')).not.toBeInTheDocument();
});
