import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { CredentialRisksPage } from './CredentialRisksPage';

afterEach(() => vi.unstubAllGlobals());
it('shows offline risk summary without rendering credential secret', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [{ id: 'risk-1', name: '生产密码检查', status: 'completed', progress: 100, offline_only: true, policy_version: '2026.09', credential_ids: ['c1'], credential_snapshot: [{ id: 'c1', name: 'prod-ssh', username: 'root', asset_count: 1 }], summary: { credentials_total: 1, at_risk: 1, high: 1, medium: 0, passed: 0 }, results: [], execution_log: [], error_message: null, rerun_of_id: null, started_at: '', finished_at: '', created_at: '', updated_at: '' }] }).mockResolvedValueOnce({ ok: true, json: async () => [] }));
  render(<ConfigProvider><CredentialRisksPage /></ConfigProvider>);
  expect(await screen.findByText('生产密码检查')).toBeInTheDocument();
  expect(screen.getByText('1 / 1')).toBeInTheDocument();
  expect(screen.getByText('离线评估')).toBeInTheDocument();
  expect(screen.queryByText('root123')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: '弱口令风险' })).toBeInTheDocument();
});
it('keeps credential risk actions available without a duplicate embedded heading', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><CredentialRisksPage embedded /></ConfigProvider>);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const page = within(screen.getByRole('region', { name: '弱口令评估' }));
  expect(page.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  expect(page.queryByText('后端内存离线策略评估')).not.toBeInTheDocument();
  fireEvent.click(page.getByRole('button', { name: '刷新弱口令任务' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
  fireEvent.click(page.getByRole('button', { name: '新建评估' }));
  const dialog = within(await screen.findByRole('dialog', { name: '新建弱口令风险评估' }));
  expect(dialog.getByText('密码型凭据')).toBeInTheDocument();
  expect(dialog.getByText('不发起登录尝试，不保存或回传密码内容')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(fetchMock.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true);
});
