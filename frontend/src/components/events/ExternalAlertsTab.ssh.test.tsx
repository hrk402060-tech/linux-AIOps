import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../../api/auth';
import { ExternalAlertsTab } from './ExternalAlertsTab';

vi.setConfig({ testTimeout: 30_000 });

vi.mock('./RecoveryVerificationPanel', () => ({ RecoveryVerificationPanel: () => null }));
vi.mock('./DiagnosticJobHistoryPanel', () => ({ DiagnosticJobHistoryPanel: () => null }));
vi.mock('./AlertNotificationHistory', () => ({ AlertNotificationHistory: () => null }));

const asset = { id: 'asset-ssh', name: '演示节点', host: '192.0.2.10', lifecycle_status: 'enabled' };
const alert = {
  id: 'alert-ssh', title: '演示磁盘告警', asset_id: asset.id, asset_name: asset.name,
  connector_id: 'demo', status: 'open', severity: 'high', payload: {},
  latest_job: null, latest_analysis: null, latest_recovery: null,
  received_at: '2026-10-20T01:00:00Z',
};
const response = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);

function setup({
  role = 'admin', assets = response([asset]), detailAssetId = asset.id,
  withCallback = true,
}: {
  role?: AuthUser['role']; assets?: Promise<Response>; detailAssetId?: string; withCallback?: boolean;
} = {}) {
  let nextList: Promise<Response> | undefined;
  let nextDetail: Promise<Response> | undefined;
  const onOpenTerminal = vi.fn();
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://localhost').pathname;
    if (init?.method && init.method !== 'GET') throw new Error('Unexpected write request');
    if (path === '/api/v1/assets') return assets;
    if (path === '/api/v1/alerts') return nextList ?? response({ total: 1, items: [alert] });
    if (path === '/api/v1/alerts/alert-ssh') return nextDetail ?? response({ ...alert, asset_id: detailAssetId });
    if (path.endsWith('/timeline') || path.endsWith('/analyses')) return response([]);
    if (path.endsWith('/recovery-verifications')) return response({
      total: 0, items: [], options: { asset_enabled: true, active_verification: false, metrics: [], probes: [], collection_mode: 'existing_evidence_only' },
    });
    if (path.endsWith('/diagnostic-jobs')) return response({ total: 0, items: [] });
    if (path.endsWith('/notifications')) return response({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <ExternalAlertsTab currentUser={{ role }} onOpenTerminal={withCallback ? onOpenTerminal : undefined} />
  </ConfigProvider>);
  return {
    fetchMock, onOpenTerminal,
    list: (result: Promise<Response>) => { nextList = result; },
    detail: (result: Promise<Response>) => { nextDetail = result; },
  };
}

async function openDetail() {
  fireEvent.click(await screen.findByRole('button', { name: '查看 演示磁盘告警' }));
  return within(await screen.findByRole('dialog', { name: /演示磁盘告警/ }));
}

afterEach(() => vi.unstubAllGlobals());

it.each(['admin', 'operator'] as const)('lets %s select the exact alert asset without connecting or executing', async (role) => {
  const harness = setup({ role });
  const drawer = await openDetail();
  const button = drawer.getByRole('button', { name: '打开 演示节点 SSH' });
  await waitFor(() => expect(button).toBeEnabled());
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(harness.onOpenTerminal).toHaveBeenCalledExactlyOnceWith('asset-ssh');
  expect(harness.fetchMock.mock.calls.every(([input]) => !String(input).includes('/terminal'))).toBe(true);
});

it.each([
  ['disabled', asset.id], ['archived', asset.id], ['enabled', 'same-name-other-id'],
])('blocks %s assets with ID %s', async (lifecycle_status, id) => {
  const harness = setup({ assets: response([{ ...asset, id, lifecycle_status }]) });
  const drawer = await openDetail();
  const button = drawer.getByRole('button', { name: '打开 演示节点 SSH' });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
});

it.each([
  { role: 'auditor' as const }, { withCallback: false },
])('hides SSH when role or callback disallows navigation: %j', async (options) => {
  setup(options);
  const drawer = await openDetail();
  expect(drawer.queryByRole('button', { name: '打开 演示节点 SSH' })).not.toBeInTheDocument();
});

it('rejects detail from another asset instead of opening its evidence or SSH', async () => {
  const harness = setup({ detailAssetId: 'unmatched-detail-id' });
  fireEvent.click(await screen.findByRole('button', { name: '查看 演示磁盘告警' }));
  expect(await screen.findByText('告警与所选目标不匹配，请返回服务监控刷新')).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
});

it('blocks SSH until the asset request completes and keeps it blocked after failure', async () => {
  let resolveAssets!: (value: Response) => void;
  const assets = new Promise<Response>((resolve) => { resolveAssets = resolve; });
  const harness = setup({ assets });
  const drawer = await openDetail();
  const button = drawer.getByRole('button', { name: '打开 演示节点 SSH' });
  expect(button).toBeDisabled();
  resolveAssets(await response({ detail: 'unavailable' }, 503));
  await screen.findByText('资产列表加载失败');
  expect(button).toBeDisabled();
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
});

it('blocks SSH while the alert list refreshes and after refresh failure', async () => {
  const harness = setup();
  const drawer = await openDetail();
  const button = drawer.getByRole('button', { name: '打开 演示节点 SSH' });
  await waitFor(() => expect(button).toBeEnabled());
  let resolveList!: (value: Response) => void;
  harness.list(new Promise<Response>((resolve) => { resolveList = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '刷新外部告警' }));
  expect(button).toBeDisabled();
  resolveList(await response({ detail: '列表不可用' }, 503));
  await screen.findByText('列表不可用');
  expect(button).toBeDisabled();
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
});

it('blocks SSH during a detail refresh and after the detail becomes stale', async () => {
  const harness = setup();
  const drawer = await openDetail();
  const button = drawer.getByRole('button', { name: '打开 演示节点 SSH' });
  await waitFor(() => expect(button).toBeEnabled());
  let resolveDetail!: (value: Response) => void;
  harness.detail(new Promise<Response>((resolve) => { resolveDetail = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '查看 演示磁盘告警' }));
  expect(button).toBeDisabled();
  resolveDetail(await response({ detail: '详情不可用' }, 503));
  await screen.findByText('详情不可用');
  expect(button).toBeDisabled();
  expect(harness.onOpenTerminal).not.toHaveBeenCalled();
});
