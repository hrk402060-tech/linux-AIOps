import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { GlobalSearch } from './GlobalSearch';
import type { SearchTarget } from './GlobalSearch';
import { getAssets } from '../api/assets';
import { getCmdbTree } from '../api/cmdb';
import { getVulnerabilities } from '../api/vulnerabilities';
import type { Asset } from '../types/asset';
import type { CmdbTreeResponse } from '../types/cmdb';
import type { VulnerabilityFinding } from '../types/vulnerability';

vi.mock('../api/assets', () => ({ getAssets: vi.fn() }));
vi.mock('../api/cmdb', () => ({ getCmdbTree: vi.fn() }));
vi.mock('../api/vulnerabilities', () => ({ getVulnerabilities: vi.fn() }));

const tree: CmdbTreeResponse = { businesses: [{
  id: 'b', code: 'shop', name: '商城', description: null, owner_user_id: null,
  lifecycle_status: 'active', lifecycle_reason: null, archived_at: null, created_at: '', updated_at: '',
  applications: [{
    id: 'app', business_id: 'b', code: 'order', name: '订单', description: null, owner_user_id: null,
    lifecycle_status: 'active', lifecycle_reason: null, archived_at: null, created_at: '', updated_at: '',
    services: [{
      id: 'svc', business_id: 'b', application_id: 'app', code: 'node-api', name: '节点服务',
      description: null, owner_user_id: null, lifecycle_status: 'active', lifecycle_reason: null,
      archived_at: null, created_at: '', updated_at: '', service_type: 'HTTP', apis: [], assets: [],
    }],
  }],
}] };
const asset: Asset = {
  id: 'asset-1', name: 'node-01', host: '192.0.2.10', ssh_port: 22, os_family: 'ubuntu',
  environment: 'production', group_name: '生产', credential_id: null, credential: null, labels: [],
  status: 'online', lifecycle_status: 'enabled', lifecycle_reason: null, lifecycle_operator: null,
  archived_at: null, latency_ms: 4, check_message: null, last_checked_at: null, created_at: '', updated_at: '',
};
const finding: VulnerabilityFinding = {
  id: 'finding-1', scan_task_id: 'scan-1', scan_task_name: 'node scan',
  asset_id: 'asset-1', asset_name: 'node-01', knowledge_version: 'kb-1',
  cve_id: 'CVE-2026-0001', advisory_id: 'vendor-1', title: '安全更新',
  severity: 'critical', package_name: 'openssl', installed_version: '1.0', fixed_version: '1.1',
  source_name: 'vendor', vendor_url: null, conclusion: 'confirmed', confidence: 'high',
  evidence_chain: [], detected_at: '', disposition_status: 'open', disposition_reason: null,
  disposition_operator: null, disposition_changed_at: '', risk_accepted_until: null,
  verification_scan_id: null, remediated_version: null, fixed_at: null, disposition_history: [], scan_history: [],
};
const emptyFindings = {
  items: [], total: 0,
  summary: { critical: 0, high: 0, medium: 0, low: 0, confirmed: 0, manual_confirmation: 0 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getAssets).mockResolvedValue([]);
  vi.mocked(getCmdbTree).mockResolvedValue(tree);
  vi.mocked(getVulnerabilities).mockResolvedValue(emptyFindings);
});
afterEach(() => vi.restoreAllMocks());

function renderSearch(onNavigate = vi.fn(), onSearchNavigate = vi.fn()) {
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <GlobalSearch onNavigate={onNavigate} onSearchNavigate={onSearchNavigate} />
  </ConfigProvider>);
  return onNavigate;
}

async function searchFor(query: string) {
  fireEvent.change(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称'), { target: { value: query } });
  const dialog = await screen.findByRole('dialog', { name: '全局搜索' });
  await userEvent.click(within(dialog).getByRole('button', { name: /搜\s*索$/ }));
}

it('opens from the visible control and navigates a common entry', async () => {
  const onNavigate = renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  expect(await screen.findByRole('dialog', { name: '全局搜索' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /活动告警/ }));
  expect(onNavigate).toHaveBeenCalledWith('alerts');
});

it('searches assets, catalog services and vulnerabilities together', async () => {
  vi.mocked(getAssets).mockResolvedValue([asset]);
  vi.mocked(getVulnerabilities).mockResolvedValue({ ...emptyFindings, items: [finding], total: 1 });
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  expect(await screen.findByText('node-01')).toBeInTheDocument();
  expect(screen.getByText('节点服务')).toBeInTheDocument();
  expect(screen.getByText('CVE-2026-0001')).toBeInTheDocument();
  await waitFor(() => expect(getVulnerabilities).toHaveBeenCalledWith(expect.objectContaining({ query: 'node' })));
});

it('opens with Ctrl K without changing the current page', async () => {
  renderSearch();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  expect(await screen.findByRole('dialog', { name: '全局搜索' })).toBeInTheDocument();
});

it('shows the Windows shortcut and accepts keyboard activation of result buttons', async () => {
  vi.spyOn(navigator, 'platform', 'get').mockReturnValue('Win32');
  const onSearchNavigate = vi.fn();
  vi.mocked(getAssets).mockResolvedValue([asset]);
  renderSearch(vi.fn(), onSearchNavigate);
  expect(screen.getByText('Ctrl K')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('192.0.2.10');
  const result = await screen.findByRole('button', { name: /node-01/ });
  result.focus();
  await userEvent.keyboard('{Enter}');
  expect(onSearchNavigate).toHaveBeenCalledWith({
    page: 'assets', assetId: 'asset-1', query: '192.0.2.10',
  });
});

it('filters services locally even when the tree includes unmatched siblings', async () => {
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('unrelated');
  expect(await screen.findByText('没有匹配结果')).toBeInTheDocument();
  expect(screen.queryByText('节点服务')).not.toBeInTheDocument();
});

it('clears stale results immediately when the input changes', async () => {
  vi.mocked(getAssets).mockResolvedValue([asset]);
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  await screen.findByRole('button', { name: /node-01/ });
  fireEvent.change(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称'), { target: { value: 'different' } });
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
  expect(screen.queryByText('没有匹配结果')).not.toBeInTheDocument();
});

it('does not allow an older response to replace the latest search', async () => {
  const older = deferred<Asset[]>();
  vi.mocked(getAssets).mockReturnValueOnce(older.promise).mockResolvedValueOnce([
    { ...asset, id: 'asset-2', name: 'new-node', host: '192.0.2.11' },
  ]);
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node-01');
  await searchFor('new-node');
  await screen.findByRole('button', { name: /new-node/ });
  await act(async () => { older.resolve([asset]); });
  expect(screen.getByRole('button', { name: /new-node/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
});

it('invalidates a pending search when the input is cleared', async () => {
  const pending = deferred<Asset[]>();
  vi.mocked(getAssets).mockReturnValueOnce(pending.promise);
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  fireEvent.change(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称'), { target: { value: '' } });
  await act(async () => { pending.resolve([asset]); });
  expect(screen.getByRole('button', { name: /活动告警/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
});

it('resets on close and rejects a late response after reopening', async () => {
  const pending = deferred<Asset[]>();
  vi.mocked(getAssets).mockReturnValueOnce(pending.promise);
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  fireEvent.keyDown(window, { key: 'Escape' });
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await act(async () => { pending.resolve([asset]); });
  expect(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称')).toHaveValue('');
  expect(screen.getByRole('button', { name: /活动告警/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
});

it('does not retain a request from an unmounted user search', async () => {
  const pending = deferred<Asset[]>();
  vi.mocked(getAssets).mockReturnValueOnce(pending.promise);
  const view = render(<GlobalSearch key="user-a" onNavigate={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  view.rerender(<GlobalSearch key="user-b" onNavigate={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await act(async () => { pending.resolve([asset]); });
  expect(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称')).toHaveValue('');
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
});

it.each(['blur', 'sentinel:session-expired'])('clears results and late requests on %s', async (event) => {
  const pending = deferred<Asset[]>();
  vi.mocked(getAssets).mockReturnValueOnce(pending.promise);
  renderSearch();
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor('node');
  act(() => window.dispatchEvent(new Event(event)));
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await act(async () => { pending.resolve([asset]); });
  expect(screen.getByPlaceholderText('输入资产、服务、CVE 或功能名称')).toHaveValue('');
  expect(screen.queryByRole('button', { name: /node-01/ })).not.toBeInTheDocument();
});

it.each<[string, RegExp, SearchTarget]>([
  ['node-api', /节点服务/, { page: 'cmdb', serviceId: 'svc' }],
  ['CVE-2026-0001', /CVE-2026-0001/, { page: 'findings', assetId: 'asset-1', query: 'CVE-2026-0001' }],
])('passes the exact %s target to navigation', async (query, label, target) => {
  vi.mocked(getVulnerabilities).mockResolvedValue({ ...emptyFindings, items: [finding], total: 1 });
  const navigate = vi.fn();
  renderSearch(vi.fn(), navigate);
  await userEvent.click(screen.getByRole('button', { name: '打开全局搜索' }));
  await searchFor(query);
  await userEvent.click(await screen.findByRole('button', { name: label }));
  expect(navigate).toHaveBeenCalledWith(target);
});
