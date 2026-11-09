import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { IncidentsPage } from './IncidentsPage';

vi.setConfig({ testTimeout: 30_000 });

const admin = { role: 'admin' as const };
const auditor = { role: 'auditor' as const };

afterEach(() => vi.unstubAllGlobals());

it('lists incidents and opens the immutable timeline', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => ({
    ok: true,
    json: async () => url.includes('/timeline')
      ? [{ status: 'open', note: '创建故障', operator: 'admin', created_at: '2026-09-18T00:00:00Z' }]
      : { total: 1, items: [{ id: 'inc-1', title: 'CPU 异常', description: '影响生产', severity: 'high', status: 'open', asset_id: 'asset-1', asset_name: 'node-1', alert_id: null, task_id: null, operator: 'admin', created_at: '2026-09-18T00:00:00Z', sla_status: 'at_risk', sla_due_at: '2026-09-18T01:00:00Z', sla_target_minutes: 60 }] },
  })));
  render(<ConfigProvider><IncidentsPage currentUser={admin} /></ConfigProvider>);
  expect(await screen.findByText('CPU 异常')).toBeInTheDocument();
  expect(screen.getByText('SLA 临近')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '查看 CPU 异常 时间线' }));
  expect(await screen.findByText('创建故障')).toBeInTheDocument();
});

it('opens incident details with linked alert diagnosis and recovery context', async () => {
  const incident = {
    id: 'inc-3', title: '接口延迟', description: '支付接口持续超时', severity: 'high',
    status: 'in_progress', asset_id: 'asset-1', asset_name: 'node-1', alert_id: 'alert-3',
    task_id: null, operator: 'admin', created_at: '2026-09-18T00:00:00Z',
    updated_at: '2026-09-18T01:00:00Z',
  };
  const fetchMock = vi.fn().mockImplementation(async (url: string) => ({
    ok: true,
    json: async () => url.endsWith('/notifications') ? { items: [] } : url.includes('/recovery-verifications')
      ? { total: 0, items: [], options: { asset_enabled: true, metrics: [], probes: [], collection_mode: 'existing_evidence_only' } }
      : url.includes('/api/v1/alerts/alert-3')
      ? {
        ...incident, id: 'alert-3', title: '支付接口告警', status: 'in_progress',
        received_at: '2026-09-18T00:30:00Z',
        latest_analysis: { status: 'completed', summary: '连接池耗尽', evidence: {} },
        latest_recovery: { status: 'verified' },
      }
      : url.includes('/timeline')
        ? [{ status: 'open', note: '创建故障', operator: 'admin', created_at: '2026-09-18T00:00:00Z' }]
        : url === '/api/v1/incidents/inc-3' ? incident : { total: 1, items: [incident] },
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><IncidentsPage currentUser={admin} /></ConfigProvider>);
  await screen.findByText('接口延迟');
  await userEvent.click(screen.getByRole('button', { name: '查看 接口延迟 详情' }));
  expect(await screen.findByText('故障详情 · 接口延迟')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-3', expect.any(Object));
  const detailDrawer = screen.getByRole('dialog', { name: '故障详情 · 接口延迟' });
  expect(detailDrawer.textContent).toContain('支付接口告警');
  expect(detailDrawer).toHaveTextContent('连接池耗尽');
  expect(await screen.findByText('暂无恢复验证')).toBeInTheDocument();
  expect(detailDrawer).toHaveTextContent('恢复验证');
  expect(detailDrawer).not.toHaveTextContent('verified');
  expect(screen.getByText('创建故障')).toBeInTheDocument();
});

it('requires a human note before advancing an incident', async () => {
  const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => ({
    ok: true,
    json: async () => init?.method === 'PATCH'
      ? { id: 'inc-1', status: 'acknowledged' }
      : { total: 1, items: [{ id: 'inc-1', title: '磁盘异常', description: '空间不足', severity: 'critical', status: 'open', asset_id: 'asset-1', asset_name: 'node-1', alert_id: null, task_id: null, operator: 'admin', created_at: '2026-09-18T00:00:00Z' }] },
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><IncidentsPage currentUser={admin} /></ConfigProvider>);
  await screen.findByText('磁盘异常');
  await userEvent.click(screen.getByRole('button', { name: '推进 磁盘异常' }));
  const confirm = screen.getByRole('button', { name: '确认推进' });
  expect(confirm).toBeDisabled();
  await userEvent.type(screen.getByRole('textbox', { name: '处置备注' }), '值班人员已确认影响范围');
  expect(confirm).toBeEnabled();
  await userEvent.click(confirm);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/incidents/inc-1/status', expect.objectContaining({ method: 'PATCH' }));
});

it('requires the explicit close phrase as well as a non-empty note', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ total: 1, items: [{ id: 'inc-2', title: '服务中断', description: '已恢复', severity: 'high', status: 'resolved', asset_id: 'asset-2', asset_name: 'node-2', alert_id: null, task_id: null, operator: 'admin', created_at: '2026-09-18T00:00:00Z' }] }),
  }));
  render(<ConfigProvider><IncidentsPage currentUser={admin} /></ConfigProvider>);
  await screen.findByText('服务中断');
  await userEvent.click(screen.getByRole('button', { name: '推进 服务中断' }));
  const confirm = screen.getByRole('button', { name: '确认推进' });
  await userEvent.type(screen.getByRole('textbox', { name: '处置备注' }), '人工核验服务已恢复');
  expect(confirm).toBeDisabled();
  expect(screen.getByText('确认关闭', { selector: 'strong' })).toBeInTheDocument();
  await userEvent.type(screen.getByRole('textbox', { name: '关闭确认文本' }), '确认关闭');
  expect(confirm).toBeEnabled();
});

it('creates incidents and sends selected filters', async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => ({
    ok: true,
    json: async () => url === '/api/v1/assets'
      ? [{ id: 'asset-1', name: 'node-1', host: '192.0.2.1' }]
      : { total: 0, items: [] },
  }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><IncidentsPage currentUser={admin} /></ConfigProvider>);
  await screen.findByText('暂无故障记录');
  await userEvent.click(screen.getByRole('button', { name: '新建故障' }));
  expect(await screen.findByRole('dialog', { name: '新建故障' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('combobox', { name: '新建故障级别' }));
  await userEvent.click(await screen.findByText('严重'));
  expect(fetchMock).toHaveBeenCalled();
});

it('keeps auditors read only', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 0, items: [] }) }));
  render(<ConfigProvider><IncidentsPage currentUser={auditor} /></ConfigProvider>);
  await screen.findByText('暂无故障记录');
  expect(screen.queryByRole('button', { name: '新建故障' })).not.toBeInTheDocument();
  expect(screen.getByText('审计员只读')).toBeInTheDocument();
});
