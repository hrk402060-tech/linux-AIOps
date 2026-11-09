import { fireEvent, render, screen, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { EventsPage } from './EventsPage';

const auditor = {
  id: 'auditor-1', username: 'auditor', display_name: '审计员', role: 'auditor' as const,
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
const analysis = {
  id: 'analysis-1', status: 'partial', error_code: 'partial_collection',
  summary: '部分动作采集失败', evidence: {}, operator: 'auditor', started_at: '2026-10-07T01:00:00Z',
};
const alert = {
  id: 'alert-1', connector_id: 'connector-1', title: '只读采集演示', severity: 'high', status: 'open',
  asset_id: 'asset-1', asset_name: 'node-1', payload: {}, received_at: '2026-10-07T01:00:00Z',
  latest_analysis: analysis,
};
function stubApi(status = 'partial') {
  const shownAnalysis = { ...analysis, status, error_code: status === 'cancelled' ? 'cancelled' : analysis.error_code };
  const shownAlert = { ...alert, latest_analysis: shownAnalysis };
  const mock = vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const body: Record<string, unknown> = {
      '/api/v1/assets': [], '/api/v1/monitoring/overview': { assets: [] }, '/api/v1/events': { items: [], total: 0 },
      '/api/v1/diagnostic-grants': { total: 0, items: [], catalog: [] },
      '/api/v1/alerts': { items: [shownAlert], total: 1 }, '/api/v1/alerts/alert-1': shownAlert,
      '/api/v1/alerts/alert-1/timeline': [], '/api/v1/alerts/alert-1/analyses': [shownAnalysis],
      '/api/v1/alerts/alert-1/diagnostic-jobs': { total: 0, items: [] },
      '/api/v1/alerts/alert-1/recovery-verifications': { total: 0, items: [], options: { asset_enabled: true, active_verification: false, metrics: [], probes: [], collection_mode: 'existing_evidence_only' } },
      '/api/v1/alert-notifications': { total: 0, items: [] },
    };
    if (!(path in body)) throw new Error(`Unexpected diagnostic tab API: ${path}`);
    return Promise.resolve({ ok: true, status: 200, json: async () => body[path] });
  });
  vi.stubGlobal('fetch', mock); return mock;
}
afterEach(() => vi.unstubAllGlobals());

it('adds grants within the existing alert page and preserves its original tabs', async () => {
  const mock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '只读诊断授权' }));
  expect(await screen.findByText('暂无只读诊断授权')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新增只读授权' })).not.toBeInTheDocument();
  expect(screen.getAllByRole('tab')).toHaveLength(5);
  expect(mock).toHaveBeenCalledWith('/api/v1/diagnostic-grants?offset=0&limit=50', expect.anything());
}, 30_000);

it('labels sealed cancelled diagnosis history with a neutral tag in the real alert drawer', async () => {
  stubApi('cancelled');
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '外部告警研判' }));
  fireEvent.click(await screen.findByRole('button', { name: '查看 只读采集演示' }));
  const drawer = await screen.findByRole('dialog', { name: '只读采集演示 · 研判详情' });
  const tag = within(drawer).getByText('已取消');
  expect(tag).toHaveClass('ant-tag');
  expect(tag).not.toHaveClass('ant-tag-red', 'ant-tag-green', 'ant-tag-blue', 'ant-tag-gold');
  expect(within(drawer).getByText('诊断历史')).toBeInTheDocument();
  expect(within(drawer).getByRole('listitem', { name: '恢复验证' })).toHaveTextContent('待验证');
}, 30_000);

it('shows incomplete diagnostic evidence consistently without asserting verified recovery', async () => {
  stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '外部告警研判' }));
  expect(await screen.findByText('部分采集失败')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看 只读采集演示' }));
  await screen.findByRole('region', { name: '告警处置流程' });
  expect(screen.getAllByText('部分采集失败').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('只读诊断部分采集失败，证据不完整，需人工排查。')).toBeInTheDocument();
  expect(within(screen.getByRole('listitem', { name: '恢复验证' })).getByText('待验证')).toBeInTheDocument();
}, 30_000);
