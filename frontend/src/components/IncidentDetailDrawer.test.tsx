import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import type { Incident } from '../api/incidents';
import { IncidentDetailDrawer } from './IncidentDetailDrawer';

const incident: Incident = {
  id: 'incident-1', title: '服务超时', description: '接口响应异常', severity: 'high',
  status: 'resolved', asset_id: 'asset-1', asset_name: 'node-1', alert_id: 'alert:alert-1',
  task_id: null, operator: 'operator', created_at: '2026-10-20T00:00:00Z',
};
const alert = {
  id: 'alert-1', asset_id: 'asset-1', incident_id: 'incident-1', title: '延迟告警',
  status: 'in_progress', received_at: '2026-10-20T00:00:00Z',
  latest_analysis: { status: 'completed', summary: '连接池耗尽' }, latest_recovery: null,
};
const alertTimeline = [
  { id: 1, alert_id: 'alert-1', event_type: 'ingested', status: 'open', note: '外部告警已验签接收', operator: 'connector:Prometheus', created_at: '2026-10-20T00:00:00Z' },
  { id: 2, alert_id: 'alert-1', event_type: 'diagnostic_finished', status: 'completed', note: '只读采集完成', operator: 'system', created_at: '2026-10-20T00:01:00Z' },
  { id: 3, alert_id: 'alert-1', event_type: 'incident_created', status: 'in_progress', note: '人工确认转为故障', operator: 'operator', created_at: '2026-10-20T00:02:00Z' },
];
const recovery = { total: 0, items: [], options: {
  asset_enabled: true, active_verification: false, metrics: [{ value: 'availability', label: '在线状态' }],
  probes: [], collection_mode: 'existing_evidence_only',
} };
const response = (body: unknown, status = 200) => ({
  ok: status < 400, status, json: async () => body,
});
function setup(options: { role?: 'admin' | 'operator' | 'auditor'; item?: Incident; linked?: object; failure?: boolean } = {}) {
  const item = options.item ?? incident;
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.endsWith('/notifications')) return response({ items: [] });
    if (url.includes('/recovery-verifications')) return response(recovery);
    if (url === '/api/v1/alerts/alert-1/timeline') return response(alertTimeline);
    if (url.startsWith('/api/v1/alerts/')) {
      return options.failure ? response({ detail: 'private-error' }, 500) : response(options.linked ?? alert);
    }
    if (url.endsWith('/timeline')) return response([
      { status: 'resolved', note: '人工登记处理结果', operator: 'operator', created_at: incident.created_at },
    ]);
    return response(item);
  });
  vi.stubGlobal('fetch', fetchMock);
  const view = render(<ConfigProvider theme={{ token: { motion: false } }}>
    <IncidentDetailDrawer incident={item} currentUser={{ role: options.role ?? 'admin' }} onClose={vi.fn()} />
  </ConfigProvider>);
  return { fetchMock, ...view };
}
afterEach(() => vi.unstubAllGlobals());

it('provides recovery verification without treating a manual resolution as verified', async () => {
  const { fetchMock } = setup();
  await screen.findByRole('region', { name: '关联告警时间线' });
  expect(await screen.findByText('连接池耗尽', { exact: false })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled(), { timeout: 15_000 });
  expect(screen.getAllByText('人工登记完成').length).toBeGreaterThan(0);
  expect(screen.queryByText('检查通过')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1', expect.any(Object));
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  expect(fetchMock.mock.calls.some(([url]) => /terminal|diagnostic-jobs/.test(url))).toBe(false);
});

it('loads incident tickets on demand without adding a write request', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/timeline')) {
      return response([{ status: 'resolved', note: '人工处理', operator: 'operator', created_at: incident.created_at }]);
    }
    if (url.endsWith('/tickets')) {
      return response([{
        id: 'ticket-1', incident_id: incident.id, title: '排查服务', description: '读取状态',
        priority: 'high', status: 'open', assignee_user_id: null, assignee_username: null,
        due_at: null, created_by: 'operator', created_at: incident.created_at,
        updated_at: incident.created_at, resolved_at: null, sla_status: 'on_track',
      }]);
    }
    return response({ ...incident, alert_id: null });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider><IncidentDetailDrawer incident={{ ...incident, alert_id: null }} currentUser={{ role: 'admin' }} onClose={vi.fn()} /></ConfigProvider>);

  await screen.findByText('工单尚未加载');
  fireEvent.click(screen.getByRole('button', { name: '加载工单' }));
  expect(await screen.findByText('排查服务')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('shows the immutable timeline for the linked alert in the incident detail', async () => {
  setup();
  const timeline = await screen.findByRole('region', { name: '关联告警时间线' });
  expect(timeline).toHaveTextContent('告警接入');
  expect(timeline).toHaveTextContent('外部告警已验签接收');
  expect(timeline).toHaveTextContent('诊断完成');
  expect(timeline).toHaveTextContent('转为故障');
  expect(timeline).toHaveTextContent('operator');
});

it('requires human confirmation before creating recovery and refreshes the incident context afterwards', async () => {
  const { fetchMock } = setup({ role: 'operator' });
  await screen.findByRole('region', { name: '关联告警时间线' });
  await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled(), { timeout: 15_000 });
  fireEvent.click(screen.getByRole('button', { name: '发起恢复验证' }));
  const confirmation = await screen.findByLabelText('恢复验证确认文本');
  const dialog = confirmation.closest<HTMLElement>('[role="dialog"]');
  expect(dialog).not.toBeNull();
  const modal = within(dialog!);
  expect(modal.getByText('发起恢复验证', { selector: 'code' })).toBeInTheDocument();
  fireEvent.change(modal.getByLabelText('验证原因'), { target: { value: ' 人工处理后验证 ' } });
  fireEvent.change(modal.getByLabelText('恢复验证确认文本'), { target: { value: '错误文本' } });
  expect(modal.getByRole('button', { name: '确认发起' })).toBeDisabled();
  fireEvent.change(modal.getByLabelText('恢复验证确认文本'), { target: { value: ' 发起恢复验证 ' } });
  expect(modal.getByRole('button', { name: '确认发起' })).toBeEnabled();
  fireEvent.click(modal.getByRole('button', { name: '确认发起' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/v1/incidents/incident-1').length).toBe(2));
  const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe('/api/v1/alerts/alert-1/recovery-verifications');
  expect(JSON.parse(writes[0][1]?.body as string)).toMatchObject({ confirmation: 'alert-1', reason: '人工处理后验证' });
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false);
}, 15_000);

it('keeps auditors read-only while still loading recovery history', async () => {
  const { fetchMock } = setup({ role: 'auditor' });
  await screen.findByText('暂无恢复验证');
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.includes('recovery-verifications'))).toBe(true);
});

it('does not request alert recovery for an unlinked incident', async () => {
  const { fetchMock } = setup({ item: { ...incident, alert_id: null } });
  expect(await screen.findByText('此故障未关联外部告警，暂无关联恢复验证。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith('/api/v1/alerts/'))).toBe(false);
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
});

it.each(['scan:scan-1', 'knowledge:entry-1', 'alert:'])('does not treat legacy source %s as an external alert', async (alertId) => {
  const { fetchMock } = setup({ item: { ...incident, alert_id: alertId } });
  expect(await screen.findByText('关联记录不是外部告警，恢复验证不可用。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith('/api/v1/alerts/'))).toBe(false);
});

it.each([
  { ...alert, id: 'another-alert' },
  { ...alert, asset_id: 'another-asset' },
  { ...alert, incident_id: 'another-incident' },
])('rejects mismatched alert context without mounting the verification panel: %j', async (linked) => {
  const { fetchMock } = setup({ linked });
  expect(await screen.findByText('关联告警与当前故障不匹配，已禁止恢复验证。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.includes('recovery-verifications'))).toBe(false);
});

it('shows a bounded error for inaccessible legacy or failed alert links', async () => {
  const { fetchMock } = setup({ failure: true });
  expect(await screen.findByText('关联告警暂不可用，请刷新重试；不会自动发起验证。')).toBeInTheDocument();
  expect(screen.queryByText('private-error')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.includes('recovery-verifications'))).toBe(false);
});

it('ignores a late detail response after closing the drawer', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  const fetchMock = vi.fn((url: string) => url.endsWith('/timeline')
    ? Promise.resolve(response([])) : new Promise((done) => { resolve = done; }));
  vi.stubGlobal('fetch', fetchMock);
  const { rerender } = render(<IncidentDetailDrawer incident={incident} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  rerender(<IncidentDetailDrawer incident={null} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  await act(async () => { resolve(response(incident)); });
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith('/api/v1/alerts/'))).toBe(false);
});

it('discards late responses when switching incident and role', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  const other = { ...incident, id: 'incident-2', title: '其他故障', alert_id: null };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/timeline')) return response([]);
    if (url.endsWith('/notifications')) return response({ items: [] });
    if (url.endsWith('/incident-1')) return new Promise((done) => { resolve = done; });
    return response(other);
  }));
  const { rerender } = render(<IncidentDetailDrawer incident={incident} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  rerender(<IncidentDetailDrawer incident={other} currentUser={{ role: 'auditor' }} onClose={vi.fn()} />);
  await screen.findByText('此故障未关联外部告警，暂无关联恢复验证。');
  await act(async () => { resolve(response(incident)); });
  expect(screen.getByRole('dialog', { name: '故障详情 · 其他故障' })).toBeInTheDocument();
  expect(screen.queryByText('连接池耗尽', { exact: false })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
});

it('discards late linked alert data after switching incidents', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  const other = { ...incident, id: 'incident-2', title: '其他故障', alert_id: null };
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/timeline')) return response([]);
    if (url.startsWith('/api/v1/alerts/')) return new Promise((done) => { resolve = done; });
    return response(url.endsWith('/incident-1') ? incident : other);
  });
  vi.stubGlobal('fetch', fetchMock);
  const { rerender } = render(<IncidentDetailDrawer incident={incident} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1', expect.any(Object)));
  rerender(<IncidentDetailDrawer incident={other} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  await screen.findByText('此故障未关联外部告警，暂无关联恢复验证。');
  await act(async () => { resolve(response(alert)); });
  expect(fetchMock.mock.calls.some(([url]) => url.includes('recovery-verifications'))).toBe(false);
  expect(screen.queryByText('延迟告警', { exact: false })).not.toBeInTheDocument();
});

it('discards administrator data when only the role changes to auditor', async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  let reads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/timeline')) return response([]);
    if (url.includes('/recovery-verifications')) return response(recovery);
    if (url.endsWith('/notifications')) return response({ items: [] });
    if (url.startsWith('/api/v1/alerts/')) {
      reads += 1;
      return reads === 1 ? new Promise((done) => { resolve = done; }) : response(alert);
    }
    return response(incident);
  }));
  const { rerender } = render(<IncidentDetailDrawer incident={incident} currentUser={{ role: 'admin' }} onClose={vi.fn()} />);
  await waitFor(() => expect(reads).toBe(1));
  rerender(<IncidentDetailDrawer incident={incident} currentUser={{ role: 'auditor' }} onClose={vi.fn()} />);
  await screen.findByText('暂无恢复验证');
  await act(async () => { resolve(response(alert)); });
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
});

it('does not offer recovery until failed detail loading is explicitly retried', async () => {
  const { fetchMock } = setup();
  await screen.findByRole('region', { name: '关联告警时间线' });
  await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled(), { timeout: 15_000 });
  fetchMock.mockResolvedValueOnce(response({ detail: 'private-error' }, 500));
  fireEvent.click(screen.getByRole('button', { name: '刷新故障详情' }));
  await screen.findByText('故障详情加载失败，请刷新重试；当前不可进行恢复验证。');
  expect(screen.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
  expect(screen.queryByText('private-error')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新故障详情' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '发起恢复验证' })).toBeEnabled(), { timeout: 15_000 });
}, 15_000);
