import { configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterAll, beforeEach, expect, it, vi } from 'vitest';
import { AutomationPage } from './AutomationPage';
import { securityCheckTabs } from '../types/security-check';

vi.mock('./automation-workbench.css', () => ({}));
vi.setConfig({ testTimeout: 15000 });
configure({ defaultHidden: true });
afterAll(() => configure({ defaultHidden: false }));

const task = {
  id: 'same-id', name: '生产基线检查', status: 'completed', progress: 100,
  asset_ids: ['asset-1'], asset_snapshot: [{ id: 'asset-1', name: '生产数据库', host: '10.0.0.8' }],
  summary: null, execution_log: [{ status: 'completed', message: '已完成配置读取', occurred_at: '2026-11-11T01:00:00Z' }],
  error_message: null, started_at: '2026-11-11T00:00:00Z', finished_at: '2026-11-11T01:00:00Z',
  created_at: '2026-11-10T00:00:00Z', updated_at: '2026-11-11T01:00:00Z',
};
let responses: Record<string, unknown>;
let failing: Set<string>;
function renderPage(onNavigate = vi.fn()) {
  render(<ConfigProvider theme={{ token: { motion: false } }}><AutomationPage onNavigate={onNavigate} /></ConfigProvider>);
  return onNavigate;
}
async function loaded() {
  return screen.findByLabelText('查看任务：生产基线检查');
}
beforeEach(() => {
  failing = new Set();
  responses = {
    '/api/v1/scan-schedules': [{
      id: 'same-id', name: '夜间漏洞计划', enabled: true, asset_ids: ['asset-2'],
      asset_snapshot: [{ name: 'Web服务器', host: '10.0.0.9' }], schedule_kind: 'daily',
      daily_time: '02:00', timezone: 'Asia/Shanghai', last_run_at: null,
      next_run_at: '2026-11-12T18:00:00Z', updated_at: task.updated_at,
    }],
    '/api/v1/baselines': [task],
    '/api/v1/exposures': [],
    '/api/v1/credential-risks': [],
    '/api/v1/patches': [{
      id: 'same-id', asset_name: 'Web服务器', package_name: 'nginx', cve_id: 'CVE-2026-12345',
      status: 'pending_approval', current_version: '1.20', target_version: '1.24',
      latest_detected_at: task.updated_at, rollback_plan: '保留旧版本软件包',
      source_name: '厂商公告', latest_scan_name: '真实扫描',
    }],
    '/api/v1/operations/approvals': [{
      id: 'approval-1', title: '生产巡检审批', status: 'pending', requested_by: '运维员',
      operation_type: 'baseline', target_id: 'baseline-1', reason: '检查生产服务器配置',
      updated_at: task.updated_at,
    }],
    '/api/v1/operations/changes': [{
      id: 'change-1', title: '生产配置变更', status: 'planned', requested_by: '管理员',
      change_type: 'configuration', target_type: 'asset', target_id: 'asset-1',
      rollback_plan: '还原已批准的配置', updated_at: task.updated_at,
    }],
    '/api/v1/operations/integrations': [{ id: 'integration-1', name: 'Prometheus', source_type: 'alertmanager', status: 'active' }],
  };
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (failing.has(url)) throw new Error('数据源暂不可用');
    return { ok: true, status: 200, json: async () => responses[url] ?? [] };
  }));
});

it('uses the confirmed title and removes introduction and policy strips', async () => {
  renderPage();
  await loaded();
  expect(screen.getByRole('heading', { name: '自动化任务', level: 1 })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: '任务队列' })).toBeInTheDocument();
  for (const text of ['查看执行队列、审批节点与变更活动', '当前策略', '默认只读', '最近同步', '今日任务队列']) {
    expect(screen.queryByText(text, { exact: false })).not.toBeInTheDocument();
  }
});

it('keeps details closed initially and opens, switches, collapses and reopens real tasks', async () => {
  renderPage();
  const row = await loaded();
  const consolePanel = screen.getByTestId('automation-console');
  expect(consolePanel).not.toHaveClass('is-detail-open');
  expect(consolePanel.querySelector('[aria-label="任务详情"]')).toHaveAttribute('aria-hidden', 'true');
  fireEvent.click(row);
  expect(consolePanel).toHaveClass('is-detail-open');
  let detail = screen.getByLabelText('任务详情');
  expect(within(detail).getByText('10.0.0.8', { exact: false })).toBeInTheDocument();
  expect(within(detail).getByText('已完成配置读取')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('收起详情'));
  expect(consolePanel).not.toHaveClass('is-detail-open');
  expect(row).toHaveFocus();
  fireEvent.click(screen.getByLabelText('查看任务：夜间漏洞计划'));
  detail = screen.getByLabelText('任务详情');
  expect(within(detail).getByRole('heading', { name: '夜间漏洞计划' })).toBeInTheDocument();
  expect(within(detail).queryByText('已完成配置读取')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('查看任务：Web服务器 · nginx'));
  expect(within(detail).getByText('CVE-2026-12345', { exact: false })).toBeInTheDocument();
});

it('searches loaded task names, targets and real execution records', async () => {
  renderPage();
  await loaded();
  const search = screen.getByLabelText('搜索任务');
  fireEvent.change(search, { target: { value: '10.0.0.8' } });
  expect(screen.getByLabelText('查看任务：生产基线检查')).toBeInTheDocument();
  expect(screen.queryByLabelText('查看任务：夜间漏洞计划')).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: '已完成配置读取' } });
  expect(screen.getByLabelText('查看任务：生产基线检查')).toBeInTheDocument();
  fireEvent.change(search, { target: { value: '不存在的任务' } });
  expect(screen.getByText('没有符合条件的任务')).toBeInTheDocument();
});

it('combines check/change and pending confirmation filters', async () => {
  renderPage();
  await loaded();
  fireEvent.click(screen.getByText('变更', { exact: true }));
  expect(screen.getByLabelText('查看任务：Web服务器 · nginx')).toBeInTheDocument();
  expect(screen.queryByLabelText('查看任务：生产基线检查')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('仅看待确认'));
  expect(screen.getByLabelText('查看任务：Web服务器 · nginx')).toBeInTheDocument();
  fireEvent.click(screen.getByText('检查', { exact: true }));
  expect(screen.getByLabelText('查看任务：生产巡检审批')).toBeInTheDocument();
  expect(screen.queryByLabelText('查看任务：生产基线检查')).not.toBeInTheDocument();
});

it.each(securityCheckTabs)('keeps the existing module shortcut to $key', async ({ key, label }) => {
  const onNavigate = renderPage();
  await loaded();
  fireEvent.click(screen.getByLabelText(`进入${key === 'credential-risks' ? '弱口令风险' : label}`));
  expect(onNavigate).toHaveBeenCalledWith(key);
});

it('uses the existing module for creation and execution records without submitting actions', async () => {
  const onNavigate = renderPage();
  fireEvent.click(await loaded());
  fireEvent.click(screen.getByText('查看执行记录', { exact: true }));
  expect(onNavigate).toHaveBeenLastCalledWith('baselines');
  fireEvent.click(screen.getByText('新建任务', { exact: true }));
  fireEvent.click(await screen.findByRole('menuitem', { name: '定时扫描' }));
  expect(onNavigate).toHaveBeenLastCalledWith('schedules');
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
});

it('preserves approvals, change records, integrations and patch entry', async () => {
  const onNavigate = renderPage();
  await loaded();
  expect(within(screen.getByRole('tabpanel')).getByText('生产巡检审批')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /变更与回滚/ }));
  expect(within(screen.getByRole('tabpanel', { name: /变更与回滚/ })).getByText('生产配置变更')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /外部告警接入/ }));
  expect(await screen.findByText('Prometheus')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('进入补丁与变更'));
  expect(onNavigate).toHaveBeenCalledWith('patches');
});

it('refreshes selected detail from the matching source and closes it when the task disappears', async () => {
  renderPage();
  fireEvent.click(await loaded());
  responses['/api/v1/baselines'] = [{ ...task, error_message: '刷新后的真实结果', status: 'failed' }];
  fireEvent.click(screen.getByLabelText('刷新自动化任务'));
  expect(await screen.findByText('刷新后的真实结果')).toBeInTheDocument();
  responses['/api/v1/baselines'] = [];
  fireEvent.click(screen.getByLabelText('刷新自动化任务'));
  await waitFor(() => expect(screen.queryByLabelText('查看任务：生产基线检查')).not.toBeInTheDocument());
  expect(screen.getByTestId('automation-console')).not.toHaveClass('is-detail-open');
});

it('retains available sources and marks failed source metrics as unknown', async () => {
  failing.add('/api/v1/scan-schedules');
  renderPage();
  await loaded();
  expect(screen.getByRole('alert')).toHaveTextContent('定时扫描');
  expect(within(screen.getByTestId('automation-metric-schedules')).getByText('—')).toBeInTheDocument();
  expect(screen.getByLabelText('查看任务：生产基线检查')).toBeInTheDocument();
});

it('keeps previous rows when their refresh fails without presenting them as fresh', async () => {
  renderPage();
  await loaded();
  failing.add('/api/v1/baselines');
  fireEvent.click(screen.getByLabelText('刷新自动化任务'));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('基线合规'));
  expect(screen.getByLabelText('查看任务：生产基线检查')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('保留上次数据');
});

it('includes real approval and change records in search, filters and record navigation', async () => {
  const onNavigate = renderPage();
  await loaded();
  fireEvent.change(screen.getByLabelText('搜索任务'), { target: { value: '生产配置变更' } });
  fireEvent.click(screen.getByLabelText('查看任务：生产配置变更'));
  const detail = screen.getByLabelText('任务详情');
  expect(within(detail).getByText('还原已批准的配置')).toBeInTheDocument();
  fireEvent.click(within(detail).getByText('查看变更记录', { exact: true }));
  expect(screen.getByRole('tab', { name: /变更与回滚/ })).toHaveAttribute('aria-selected', 'true');
  fireEvent.change(screen.getByLabelText('搜索任务'), { target: { value: '生产巡检审批' } });
  fireEvent.click(screen.getByLabelText('查看任务：生产巡检审批'));
  expect(within(detail).getByText('检查生产服务器配置')).toBeInTheDocument();
  fireEvent.click(within(detail).getByText('查看审批记录', { exact: true }));
  expect(screen.getByRole('tab', { name: /审批队列/ })).toHaveAttribute('aria-selected', 'true');
  expect(onNavigate).not.toHaveBeenCalled();
});

it('closes a detail when refreshed ordering moves its task off the visible page', async () => {
  renderPage();
  fireEvent.click(await loaded());
  responses['/api/v1/baselines'] = [
    ...Array.from({ length: 8 }, (_, index) => ({
      ...task, id: `new-${index}`, name: `新增检查${index}`, updated_at: '2026-11-12T01:00:00Z',
    })),
    task,
  ];
  fireEvent.click(screen.getByLabelText('刷新自动化任务'));
  await screen.findByLabelText('查看任务：新增检查0');
  expect(screen.getByTestId('automation-console')).not.toHaveClass('is-detail-open');
  expect(screen.queryByLabelText('查看任务：生产基线检查')).not.toBeInTheDocument();
});
