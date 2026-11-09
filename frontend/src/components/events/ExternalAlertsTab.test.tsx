import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AlertStatus, ExternalAlert } from '../../api/externalAlerts';
import { ExternalAlertsTab } from './ExternalAlertsTab';

vi.setConfig({ testTimeout: 30_000 });

const asset = {
  id: 'asset-1', name: '演练节点', host: '192.0.2.10', ssh_port: 22,
  os_family: 'openEuler', environment: 'test', group_name: '演练',
  credential_id: null, credential: null, labels: ['preview-only'],
  status: 'online', lifecycle_status: 'enabled', lifecycle_reason: null,
  lifecycle_operator: null, archived_at: null, latency_ms: 8,
  check_message: null, last_checked_at: null, created_at: '', updated_at: '',
};

const baseAlert = {
  id: 'alert-1', connector_id: 'connector-1', connector_name: '演练 Webhook',
  external_id: 'demo-1', title: '演练 CPU 告警', description: '测试告警',
  severity: 'high', status: 'acknowledged', asset_id: asset.id, asset_name: asset.name,
  payload: { labels: { preview_only: 'true' } }, incident_id: null, occurrence_count: 1,
  received_at: '2026-10-20T01:00:00Z', updated_at: '2026-10-20T01:00:00Z',
  latest_analysis: null, latest_job: null, latest_recovery: null,
  notification_summary: { total: 0, sent: 0, failed: 0, blocked: 0, pending: 0, acknowledged: 0 },
} as ExternalAlert;

const jsonResponse = (body: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body } as Response);

type ApiHarness = {
  fetchMock: ReturnType<typeof vi.fn>;
  failNextDetail: (response: Response) => void;
  timeline: Array<Record<string, unknown>>;
};

function createHarness(initial: Partial<ExternalAlert> = {}, statusWrite?: (
  status: AlertStatus, note: string,
) => Promise<Response>): ApiHarness {
  let currentAlert: ExternalAlert = { ...baseAlert, ...initial };
  let nextDetailResponse: Response | null = null;
  let currentTimeline: Array<Record<string, unknown>> = [{
    id: 1, alert_id: currentAlert.id, event_type: 'ingested', previous_status: null,
    status: currentAlert.status, note: '告警接入', operator: 'system', trace_id: 'trace-1',
    details: {}, created_at: '2026-10-20T01:00:00Z',
  }];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    if (method === 'GET' && url.pathname === '/api/v1/assets') return jsonResponse([asset]);
    if (method === 'GET' && url.pathname === '/api/v1/alerts') return jsonResponse({ total: 1, items: [currentAlert] });
    if (method === 'GET' && url.pathname === `/api/v1/alerts/${currentAlert.id}`) {
      if (nextDetailResponse) {
        const response = nextDetailResponse;
        nextDetailResponse = null;
        return Promise.resolve(response);
      }
      return jsonResponse(currentAlert);
    }
    if (method === 'GET' && url.pathname.endsWith('/timeline')) return jsonResponse(currentTimeline);
    if (method === 'GET' && url.pathname.endsWith('/analyses')) return jsonResponse([]);
    if (method === 'GET' && url.pathname.endsWith('/recovery-verifications')) {
      return jsonResponse({
        total: 0, items: [], options: {
          asset_enabled: true, active_verification: false,
          metrics: [{ value: 'availability', label: '在线状态' }],
          probes: [], collection_mode: 'existing_evidence_only',
        },
      });
    }
    if (method === 'GET' && url.pathname.endsWith('/diagnostic-jobs')) return jsonResponse({ total: 0, items: [] });
    if (method === 'GET' && url.pathname.endsWith('/notifications')) return jsonResponse({ items: [] });
    if (method === 'PATCH' && url.pathname.endsWith('/status')) {
      const body = JSON.parse(String(init?.body)) as { status: AlertStatus; note: string };
      const result = statusWrite ? statusWrite(body.status, body.note) : jsonResponse(currentAlert);
      return result.then((response) => {
        if (response.ok) {
          currentAlert = { ...currentAlert, status: body.status, updated_at: '2026-10-20T01:05:00Z' };
          currentTimeline = [...currentTimeline, {
            id: currentTimeline.length + 1, alert_id: currentAlert.id, event_type: 'status',
            previous_status: baseAlert.status, status: body.status, note: body.note,
            operator: 'operator', trace_id: `trace-${currentTimeline.length + 1}`, details: {},
            created_at: '2026-10-20T01:05:00Z',
          }];
        }
        return response;
      });
    }
    if (method === 'POST' && url.pathname.endsWith('/incident')) {
      currentAlert = { ...currentAlert, incident_id: 'incident-1' };
      currentTimeline = [...currentTimeline, {
        id: currentTimeline.length + 1, alert_id: currentAlert.id, event_type: 'incident_created',
        previous_status: currentAlert.status, status: currentAlert.status, note: '人工转为故障',
        operator: 'operator', trace_id: `trace-${currentTimeline.length + 1}`, details: {},
        created_at: '2026-10-20T01:06:00Z',
      }];
      return jsonResponse({ id: 'incident-1' }, 201);
    }
    throw new Error(`Unexpected API request: ${method} ${url.pathname}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    fetchMock,
    failNextDetail: (response: Response) => { nextDetailResponse = response; },
    timeline: currentTimeline,
  };
}

function show(role: 'admin' | 'operator' | 'auditor', initial?: Partial<ExternalAlert>) {
  const harness = createHarness(initial);
  renderTab(role);
  return harness;
}

function renderTab(role: 'admin' | 'operator' | 'auditor', onOpenIncidents?: () => void) {
  render(<ConfigProvider theme={{ token: { motion: false } }}>
    <ExternalAlertsTab currentUser={{ role }} onOpenIncidents={onOpenIncidents} />
  </ConfigProvider>);
}

async function openDetail(title = baseAlert.title) {
  await screen.findByText(title);
  fireEvent.click(screen.getByRole('button', { name: `查看 ${title}` }));
  return within(await screen.findByRole('dialog', { name: new RegExp(title) }));
}

afterEach(() => vi.unstubAllGlobals());

describe('ExternalAlertsTab detail workflow', () => {
  it.each(['admin', 'operator'] as const)('allows %s to advance from detail and refreshes recovery actions', async (role) => {
    const user = userEvent.setup();
    const harness = show(role);
    const drawer = await openDetail();
    await waitFor(() => expect(drawer.getByRole('button', { name: '发起恢复验证' })).toBeDisabled());
    expect(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` })).toBeEnabled();
    fireEvent.click(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` }));
    await screen.findByText('人工推进告警');
    const modal = screen;
    const input = modal.getByRole('textbox', { name: '推进说明' });
    const confirm = modal.getByRole('button', { name: '确认推进' });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: '   ' } });
    expect(confirm).toBeDisabled();
    expect(harness.fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
    fireEvent.change(input, { target: { value: '  人工完成排查  ' } });
    await user.click(modal.getByRole('button', { name: '确认推进' }));
    await waitFor(() => expect(drawer.getByText('人工处理中')).toBeInTheDocument());
    await waitFor(() => expect(drawer.getByRole('button', { name: '发起恢复验证' })).toBeEnabled());
    expect(harness.fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/status', expect.objectContaining({
      method: 'PATCH', body: JSON.stringify({ status: 'in_progress', note: '人工完成排查' }),
    }));
  });

  it('refreshes incident linkage and prevents repeated conversion', async () => {
    const user = userEvent.setup();
    const harness = show('admin', { status: 'in_progress' });
    let drawer = await openDetail();
    const action = drawer.getByRole('button', { name: `详情转为故障 ${baseAlert.title}` });
    fireEvent.click(action);
    await screen.findByText('人工转为故障');
    const modal = screen;
    const input = modal.getByRole('textbox', { name: '转故障说明' });
    const confirm = modal.getByRole('button', { name: '确认转为故障' });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: '  ' } });
    expect(confirm).toBeDisabled();
    expect(harness.fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.change(input, { target: { value: '  业务影响已确认  ' } });
    await user.click(modal.getByRole('button', { name: '确认转为故障' }));
    await waitFor(() => expect(harness.fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/incident', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ description: '业务影响已确认' }),
    })));
    drawer = within(await screen.findByRole('dialog', { name: new RegExp(baseAlert.title) }));
    await waitFor(() => expect(drawer.queryByRole('button', { name: `详情转为故障 ${baseAlert.title}` })).not.toBeInTheDocument());
    expect(harness.fetchMock.mock.calls.filter(([url]) => String(url) === '/api/v1/alerts/alert-1')).toHaveLength(2);
  }, 30_000);

  it('offers the linked incident after human conversion', async () => {
    const user = userEvent.setup();
    const onOpenIncidents = vi.fn();
    const harness = createHarness({ status: 'in_progress' });
    renderTab('admin', onOpenIncidents);
    const drawer = await openDetail();
    fireEvent.click(drawer.getByRole('button', { name: `详情转为故障 ${baseAlert.title}` }));
    await screen.findByText('人工转为故障');
    const modal = screen;
    fireEvent.change(modal.getByRole('textbox', { name: '转故障说明' }), { target: { value: '关联故障' } });
    await user.click(modal.getByRole('button', { name: '确认转为故障' }));
    const linked = await screen.findByRole('button', { name: `查看关联故障 ${baseAlert.title}` });
    await user.click(linked);
    expect(onOpenIncidents).toHaveBeenCalledOnce();
    expect(harness.fetchMock.mock.calls.filter(([url]) => String(url).includes('/incident')).length).toBeGreaterThan(0);
  }, 30_000);

  it('keeps detail write actions hidden for auditors', async () => {
    const harness = show('auditor', { status: 'in_progress' });
    const drawer = await openDetail('演练 CPU 告警');
    expect(drawer.queryByRole('button', { name: /详情推进|详情转为故障/ })).not.toBeInTheDocument();
    expect(drawer.queryByRole('button', { name: '发起恢复验证' })).not.toBeInTheDocument();
    expect(drawer.getByRole('region', { name: '恢复验证' })).toBeInTheDocument();
    expect(harness.fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });

  it('renders all recovery timeline events with Chinese labels', async () => {
    const harness = createHarness({ status: 'in_progress' });
    const events = [
      ['recovery_observing', '恢复验证观察中'],
      ['recovery_verified', '恢复检查通过'],
      ['recovery_failed', '恢复检查失败'],
      ['recovery_blocked', '恢复验证受阻'],
    ];
    harness.timeline.push(...events.map(([event_type], index) => ({
      id: index + 2, alert_id: baseAlert.id, event_type, previous_status: 'in_progress',
      status: 'in_progress', note: '演练时间线', operator: 'operator',
      trace_id: `trace-${index + 2}`, details: {}, created_at: `2026-10-20T01:0${index + 1}:00Z`,
    })));
    renderTab('auditor');
    const drawer = await openDetail();
    for (const [, label] of events) expect(drawer.getByText(`${label} · 处理中`)).toBeInTheDocument();
    expect(drawer.queryByText(/其他事件/)).not.toBeInTheDocument();
  });

  it.each([
    ['resolved', '人工登记完成'],
    ['closed', '人工已关闭'],
  ] as const)('does not present manually %s alerts as verified recovery', async (status, label) => {
    show('auditor', { status });
    expect(await screen.findByText(label)).toBeInTheDocument();
    const drawer = await openDetail();
    expect(drawer.getAllByText(label).length).toBeGreaterThan(0);
    const recoveryStage = within(drawer.getByRole('listitem', { name: '恢复验证' }));
    expect(recoveryStage.getByText('待验证')).toBeInTheDocument();
    expect(recoveryStage.queryByText('检查通过')).not.toBeInTheDocument();
    expect(drawer.queryByText('已解决')).not.toBeInTheDocument();
  });

  it('submits a detail advance only once on double click', async () => {
    let resolveWrite: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => { resolveWrite = resolve; });
    const harness = createHarness({}, () => pending);
    const user = userEvent.setup();
    renderTab('admin');
    const drawer = await openDetail();
    fireEvent.click(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` }));
    await screen.findByText('人工推进告警');
    const modal = screen;
    fireEvent.change(modal.getByRole('textbox', { name: '推进说明' }), { target: { value: '双击防护' } });
    const confirm = modal.getByRole('button', { name: '确认推进' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(harness.fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
    resolveWrite(await jsonResponse({ ...baseAlert, status: 'in_progress' }));
    await waitFor(() => expect(drawer.getByText('人工处理中')).toBeInTheDocument());
    expect(harness.fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
  });

  it('does not claim successful treatment after the status update fails', async () => {
    const harness = createHarness({}, () => jsonResponse({ detail: '状态服务失败' }, 503));
    const user = userEvent.setup();
    renderTab('admin');
    const drawer = await openDetail();
    fireEvent.click(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` }));
    await screen.findByText('人工推进告警');
    const modal = screen;
    fireEvent.change(modal.getByRole('textbox', { name: '推进说明' }), { target: { value: '失败回归' } });
    await user.click(modal.getByRole('button', { name: '确认推进' }));
    expect(await screen.findByText('状态服务失败')).toBeInTheDocument();
    const treatmentStage = within(drawer.getByRole('listitem', { name: '人工处理' }));
    expect(treatmentStage.getByText('已确认')).toBeInTheDocument();
    expect(drawer.queryByText('人工处理中')).not.toBeInTheDocument();
    expect(drawer.getByRole('button', { name: '发起恢复验证' })).toBeDisabled();
    expect(screen.queryByText('告警已推进至处理中')).not.toBeInTheDocument();
    expect(harness.fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1);
  });

  it('disables stale detail actions when the post-write refresh fails', async () => {
    const harness = createHarness();
    const user = userEvent.setup();
    renderTab('admin');
    const drawer = await openDetail();
    harness.failNextDetail(await jsonResponse({ detail: '详情暂不可用' }, 503));
    fireEvent.click(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` }));
    await screen.findByText('人工推进告警');
    const modal = screen;
    await user.type(modal.getByRole('textbox', { name: '推进说明' }), '刷新失败回归');
    await user.click(modal.getByRole('button', { name: '确认推进' }));
    expect(await screen.findByText('详情暂不可用')).toBeInTheDocument();
    await waitFor(() => expect(drawer.getByRole('button', { name: `详情推进 ${baseAlert.title}` })).toBeDisabled());
    expect(drawer.getAllByText('已确认').length).toBeGreaterThan(0);
    expect(drawer.queryByText('处理中')).not.toBeInTheDocument();
  }, 30_000);
});
