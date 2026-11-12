import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { EventsPage } from './EventsPage';

const admin = {
  id: 'admin-1', username: 'admin', display_name: '管理员', role: 'admin' as const,
  enabled: true, must_change_password: false, created_at: '', last_login_at: null,
};
const auditor = { ...admin, id: 'audit-1', username: 'auditor', role: 'auditor' as const };
const policy = {
  asset_id: 'asset-1', cpu_threshold: 85, memory_threshold: 85, swap_threshold: 80,
  disk_threshold: 85, inode_threshold: 85, load_threshold: 8, duration_minutes: 5,
  recovery_notify: true, maintenance_start: null, maintenance_end: null,
};
const externalAlert = {
  id: 'alert-1', connector_id: 'connector-1', connector_name: '生产 Prometheus', external_id: 'prom-100',
  title: 'Prometheus CPU 告警', description: 'CPU 持续高于阈值', severity: 'high',
  status: 'open', asset_id: 'asset-1', asset_name: 'node-1',
  payload: { instance: 'node-1', token: '***' },
  received_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z', incident_id: null, occurrence_count: 3,
  latest_analysis: {
    id: 'analysis-1', alert_id: 'alert-1', asset_id: 'asset-1', status: 'completed',
    trigger_type: 'automatic', policy_version: 'v1', summary: '持续高负载进程',
    evidence: { CPU: '96%' }, error_code: null as string | null, operator: 'system', trace_id: 'trace-1',
    started_at: '2026-09-18T00:00:00Z', finished_at: '2026-09-18T00:01:00Z',
  },
};
const alertTimeline = [{
  id: 1, alert_id: 'alert-1', event_type: 'ingested', previous_status: null,
  status: 'open', note: '外部告警接入', operator: 'system', trace_id: 'trace-1',
  details: {}, created_at: '2026-09-18T00:00:00Z',
}];

function response(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

function stubApi(analysisStatus = 'completed', rotateResponse?: Promise<unknown>, override: Partial<typeof externalAlert> = {}) {
  const shownAlert = { ...externalAlert, ...override };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const route = `${init?.method ?? 'GET'} ${url.pathname}`;
    if (route === 'GET /api/v1/assets') return response([{
      id: 'asset-1', name: 'node-1', host: '192.0.2.10', ssh_port: 22,
      os_family: 'openeuler', environment: 'production', group_name: '核心区',
      credential_id: null, credential: null, labels: [], status: 'online',
      lifecycle_status: 'enabled', lifecycle_reason: null, lifecycle_operator: null,
      archived_at: null, latency_ms: 10, check_message: null, last_checked_at: null,
      created_at: '', updated_at: '',
    }]);
    if (route === 'GET /api/v1/monitoring/overview') return response({ assets: [{ asset_id: 'asset-1', asset_name: 'node-1', policy }] });
    if (route === 'GET /api/v1/diagnostic-grants') return response({ total: 0, items: [], catalog: [] });
    if (route === 'GET /api/v1/events') return response({ total: 1, items: [{
      id: 'monitoring:test', title: 'CPU 高负载', asset_name: 'node-1', asset_id: 'asset-1',
      severity: 'high', source: 'monitoring', status: 'open', recovered: true,
      occurred_at: '2026-09-18T00:00:00Z',
    }] });
    if (route === 'GET /api/v1/alerts/alert-1/timeline') return response(alertTimeline);
    if (route === 'GET /api/v1/alerts/alert-1/notifications') return response({ items: [] });
    if (route === 'GET /api/v1/alert-notifications') return response({ total: 0, items: [] });
    if (route === 'GET /api/v1/alert-notification-config') return response({ configured: false, enabled: false, host: '', port: 587, security: 'starttls', username: '', sender: '', recipients: [], password_configured: false, updated_at: null });
    if (route === 'GET /api/v1/alerts/alert-1/analyses') return response([shownAlert.latest_analysis]);
    if (route === 'GET /api/v1/alerts/alert-1/diagnostic-jobs') return response({ total: 0, items: [] });
    if (route === 'GET /api/v1/alerts/alert-1/recovery-verifications') return response({ total: 0, items: [], options: { asset_enabled: true, active_verification: false, metrics: [], probes: [], collection_mode: 'existing_evidence_only' } });
    if (route === 'POST /api/v1/alerts/alert-1/diagnostics') return response({ id: 'job-1', alert_id: 'alert-1', status: analysisStatus, display_status: analysisStatus });
    if (route === 'GET /api/v1/alerts/alert-1') return response(shownAlert);
    if (route === 'GET /api/v1/alerts') return response({ total: 1, items: [shownAlert] });
    if (route === 'GET /api/v1/alert-connectors') return response([{ id: 'connector-1', name: '生产 Prometheus', source_type: 'prometheus', status: 'active', secret_masked: '****7A2F', operator: 'admin', created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z', rotated_at: null }]);
    if (route === 'PATCH /api/v1/alerts/alert-1/status') return response(externalAlert);
    if (route === 'POST /api/v1/alerts/alert-1/incident') return response({ incident_id: 'incident-1' });
    if (route === 'POST /api/v1/alert-connectors/connector-1/rotate') {
      return rotateResponse ?? response({ secret: 'test-only-rotation-secret' });
    }
    throw new Error(`Unexpected API request: ${route}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

it('service_health: carries the selected asset into alert and event filters without executing', async () => {
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor}
    initialAssetId="asset-1" initialTab="external" /></ConfigProvider>);
  await screen.findByText('Prometheus CPU 告警');
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/v1/alerts?')
    && new URL(String(url), 'http://localhost').searchParams.get('asset_id') === 'asset-1')).toBe(true);
  fireEvent.click(screen.getByRole('tab', { name: '活动告警' }));
  await screen.findByText('CPU 高负载');
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/v1/events?')
    && new URL(String(url), 'http://localhost').searchParams.get('asset_id') === 'asset-1')).toBe(true);
  expect(fetchMock.mock.calls.some(([, init]) => init?.method && init.method !== 'GET')).toBe(false);
}, 15_000);

it('adds administrator notification configuration to the existing alert page without a new route', async () => {
  stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '管理员通知' }));
  expect(await screen.findByText('管理员通知通道')).toBeInTheDocument();
  expect(await screen.findByText('暂无通知记录')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '保存通知配置' })).not.toBeInTheDocument();
}, 30_000);

it('keeps missing authorization consistent in the alert list, flow and diagnosis history', async () => {
  stubApi('failed', undefined, { latest_analysis: {
    ...externalAlert.latest_analysis, status: 'failed',
    error_code: 'diagnostic_authorization_required', summary: '缺少专用只读授权',
  } });
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '外部告警研判' }));
  expect(await screen.findByText('待授权')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看 Prometheus CPU 告警' }));
  await screen.findByRole('region', { name: '告警处置流程' });
  expect(screen.getAllByText('待授权').length).toBeGreaterThanOrEqual(2);
  expect(screen.queryByText('研判失败')).not.toBeInTheDocument();
  expect(screen.getByText('专用只读身份待授权，未连接服务器、未执行命令。')).toBeInTheDocument();
}, 30_000);

it('opens the enabled alert asset in SSH without starting a session', async () => {
  const user = userEvent.setup();
  const onOpenTerminal = vi.fn();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} onOpenTerminal={onOpenTerminal} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await user.click(await screen.findByRole('button', { name: '查看 Prometheus CPU 告警' }));
  const drawer = await screen.findByRole('dialog', { name: /Prometheus CPU 告警/ });
  await waitFor(() => expect(within(drawer).getByRole('button', { name: '打开 node-1 SSH' })).toBeEnabled());
  await user.click(within(drawer).getByRole('button', { name: '打开 node-1 SSH' }));
  expect(onOpenTerminal).toHaveBeenCalledWith('asset-1');
  expect(fetchMock.mock.calls.some(([, init]) => init?.method && init.method !== 'GET')).toBe(false);
}, 30_000);

it('shows the real workflow blockers in external alert details after manual resolution', async () => {
  stubApi('failed', undefined, { status: 'resolved' });
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '外部告警研判' }));
  fireEvent.click(await screen.findByRole('button', { name: '查看 Prometheus CPU 告警' }));
  const flow = within(await screen.findByRole('region', { name: '告警处置流程' }));
  expect(document.querySelector('.ant-drawer-content-wrapper')).toHaveStyle({ width: 'min(680px, 100vw)' });
  expect(flow.getByText('人工登记完成')).toBeInTheDocument();
  expect(flow.getByText('尚无通知记录')).toBeInTheDocument();
  expect(flow.getByText('专用只读授权待配置')).toBeInTheDocument();
  expect(flow.getByText('待验证')).toBeInTheDocument();
  expect(flow.queryByText('恢复已验证')).not.toBeInTheDocument();
  expect(screen.getByText('不可变时间线')).toBeInTheDocument();
}, 30_000);

it('keeps activity events and source recovery on the first tab', async () => {
  const user = userEvent.setup();
  stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  expect(await screen.findByText('CPU 高负载')).toBeInTheDocument();
  expect(screen.getByText('源已恢复')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '告警策略' }));
  expect(await screen.findByText('服务器告警策略')).toBeInTheDocument();
}, 30_000);

it('filters external alerts and shows immutable evidence and RCA details', async () => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  expect(await screen.findByText('Prometheus CPU 告警')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '查看 Prometheus CPU 告警' }));
  const drawer = await screen.findByRole('dialog', { name: /Prometheus CPU 告警/ });
  expect(within(drawer).getAllByText('持续高负载进程')[0]).toBeInTheDocument();
  expect(within(drawer).getByText(/后端未提供量化数据/)).toBeInTheDocument();
  expect(within(drawer).getByText(/历史研判结果不代表已获得授权/)).toBeInTheDocument();
  expect(within(drawer).getByText('外部告警接入')).toBeInTheDocument();
  expect(within(drawer).getByText('接收次数')).toBeInTheDocument();
  expect(within(drawer).getByText('3 次')).toBeInTheDocument();
  expect(within(drawer).getByText('告警接入 · 待确认')).toBeInTheDocument();
  expect(within(drawer).getByText('96%')).toBeInTheDocument();
  expect(within(drawer).getByText(/"token": "\*\*\*"/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1', expect.any(Object));
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/timeline', expect.any(Object));
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/analyses?offset=0&limit=20', expect.any(Object));
}, 30_000);

it('uses exact asset filtering and bounded server pagination', async () => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  expect(await screen.findByText('Prometheus CPU 告警')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/alerts?offset=0&limit=20', expect.any(Object),
  );
  await user.click(screen.getByRole('combobox', { name: '外部告警资产' }));
  await user.click(await screen.findByText('node-1 · 192.0.2.10'));
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/alerts?asset_id=asset-1&offset=0&limit=20', expect.any(Object),
  );
}, 30_000);

it('requires a trimmed human note before advancing status', async () => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await screen.findByText('Prometheus CPU 告警');
  await user.click(screen.getByRole('button', { name: '推进 Prometheus CPU 告警' }));
  const dialog = await screen.findByRole('dialog', { name: '人工推进告警' });
  const input = within(dialog).getByRole('textbox', { name: '推进说明' });
  const confirm = within(dialog).getByRole('button', { name: '确认推进' });
  await user.type(input, '   ');
  expect(confirm).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alerts/alert-1/status', expect.any(Object));
  await user.clear(input);
  await user.type(input, '  人工确认告警有效  ');
  await user.click(confirm);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/status', expect.objectContaining({
    method: 'PATCH', body: JSON.stringify({ status: 'acknowledged', note: '人工确认告警有效' }),
  })));
}, 30_000);

it('creates an incident only after human confirmation and trims its description', async () => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await screen.findByText('Prometheus CPU 告警');
  await user.click(screen.getByRole('button', { name: '转为故障 Prometheus CPU 告警' }));
  const dialog = await screen.findByRole('dialog', { name: '人工转为故障' });
  const input = within(dialog).getByRole('textbox', { name: '转故障说明' });
  const confirm = within(dialog).getByRole('button', { name: '确认转为故障' });
  await user.type(input, '   ');
  expect(confirm).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alerts/alert-1/incident', expect.any(Object));
  await user.clear(input);
  await user.type(input, '  业务请求受到影响  ');
  await user.click(confirm);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/incident', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ description: '业务请求受到影响' }),
  })));
}, 30_000);

it.each([
  ['推进', '人工推进告警', '/status'],
  ['转为故障', '人工转为故障', '/incident'],
])('does not submit a cancelled %s action', async (action, title, endpoint) => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await screen.findByText('Prometheus CPU 告警');
  await user.click(screen.getByRole('button', { name: action + ' Prometheus CPU 告警' }));
  const dialog = await screen.findByRole('dialog', { name: title });
  await user.click(within(dialog).getByRole('button', { name: /Cancel|取\s*消/ }));
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alerts/alert-1' + endpoint, expect.any(Object));
}, 30_000);

it.each([
  ['failed', '任务未启动，请查看失败原因和通知记录'],
  ['queued', '只读诊断已入队，不代表根因或恢复已验证'],
  ['running', '只读诊断已入队，不代表根因或恢复已验证'],
])('reports the accepted %s job without claiming completed diagnosis', async (status, feedback) => {
  const user = userEvent.setup();
  const fetchMock = stubApi(status);
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await screen.findByText('Prometheus CPU 告警');
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostics', expect.any(Object));
  await user.click(screen.getByRole('button', { name: '研判 Prometheus CPU 告警' }));
  const dialog = await screen.findByRole('dialog', { name: '人工提交只读诊断' });
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostics', expect.any(Object));
  fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断确认文本' }), { target: { value: '  提交只读诊断  ' } });
  fireEvent.change(within(dialog).getByRole('textbox', { name: '诊断操作原因' }), { target: { value: '  人工诊断  ' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '确认入队' }));
  expect(await screen.findByText(feedback)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostics', expect.objectContaining({ body: JSON.stringify({ confirmation: 'alert-1', reason: '人工诊断' }) }));
  expect(screen.queryByText('只读证据已采集，需人工研判；未验证根因或恢复')).not.toBeInTheDocument();
}, 30_000);

it('confirms rotation once and removes the one-time secret when dismissed', async () => {
  let finishRotation: (value: unknown) => void = () => { throw new Error('Rotation promise is not ready'); };
  const pending = new Promise<unknown>((resolve) => { finishRotation = resolve; });
  const fetchMock = stubApi('completed', pending);
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  fireEvent.click(screen.getByRole('tab', { name: '接入连接器' }));
  const rotate = await screen.findByRole('button', { name: '轮换 生产 Prometheus 密钥' });
  fireEvent.click(rotate);
  let dialog = await screen.findByRole('dialog', { name: '确认轮换接入密钥' });
  fireEvent.click(within(dialog).getByRole('button', { name: /取\s*消/ }));
  expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/alert-connectors/connector-1/rotate', expect.any(Object));
  fireEvent.click(rotate);
  dialog = await screen.findByRole('dialog', { name: '确认轮换接入密钥' });
  const confirm = within(dialog).getByRole('button', { name: '确认轮换' });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByRole('textbox', { name: '轮换确认文本' }), { target: { value: 'wrong' } });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByRole('textbox', { name: '轮换确认文本' }), { target: { value: '  生产 Prometheus  ' } });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-connectors/connector-1/rotate', expect.objectContaining({ body: JSON.stringify({ confirmation: '生产 Prometheus' }) }));
  expect(confirm).toBeDisabled();
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/rotate'))).toHaveLength(1);
  finishRotation({ ok: true, status: 200, json: async () => ({ secret: 'test-only-rotation-secret' }) });
  const secretDialog = await screen.findByRole('dialog', { name: '一次性接入密钥' });
  expect(within(secretDialog).getByText('test-only-rotation-secret')).toBeInTheDocument();
  fireEvent.click(within(secretDialog).getByRole('button', { name: '我已安全保存' }));
  await waitFor(() => expect(screen.queryByText('test-only-rotation-secret')).not.toBeInTheDocument());
  expect(screen.getByText('****7A2F')).toBeInTheDocument();
}, 30_000);

it('retains rotation confirmation after an HTTP failure and allows an explicit retry', async () => {
  const user = userEvent.setup();
  const fetchMock = stubApi();
  const normalRequest = fetchMock.getMockImplementation()!;
  let rotationAttempts = 0;
  fetchMock.mockImplementation((input, init) => {
    if (String(input).endsWith('/connector-1/rotate') && init?.method === 'POST') {
      rotationAttempts += 1;
      if (rotationAttempts === 1) return Promise.resolve({
        ok: false, status: 503, json: async () => ({ detail: '密钥轮换服务暂不可用' }),
      });
    }
    return normalRequest(input, init);
  });
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={admin} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '接入连接器' }));
  await user.click(await screen.findByRole('button', { name: '轮换 生产 Prometheus 密钥' }));
  const dialog = await screen.findByRole('dialog', { name: '确认轮换接入密钥' });
  const confirm = within(dialog).getByRole('button', { name: '确认轮换' });
  await user.click(confirm);
  expect(rotationAttempts).toBe(0);
  await user.type(within(dialog).getByRole('textbox', { name: '轮换确认文本' }), '生产 Prometheus');
  await user.click(confirm);
  expect(await screen.findByText('密钥轮换服务暂不可用')).toBeInTheDocument();
  expect(dialog).toBeInTheDocument();
  await waitFor(() => expect(confirm).toBeEnabled());
  expect(screen.queryByRole('dialog', { name: '一次性接入密钥' })).not.toBeInTheDocument();
  expect(rotationAttempts).toBe(1);
  await user.click(confirm);
  const secretDialog = await screen.findByRole('dialog', { name: '一次性接入密钥' });
  expect(within(secretDialog).getByText('test-only-rotation-secret')).toBeInTheDocument();
  expect(rotationAttempts).toBe(2);
}, 30_000);

it('shows masked connector secrets and hides every write action from auditors', async () => {
  const user = userEvent.setup();
  stubApi();
  render(<ConfigProvider theme={{ token: { motion: false } }}><EventsPage currentUser={auditor} /></ConfigProvider>);
  await user.click(screen.getByRole('tab', { name: '接入连接器' }));
  expect(await screen.findByText('HMAC Webhook 接入')).toBeInTheDocument();
  expect(screen.getByText('timestamp.nonce.raw_body')).toBeInTheDocument();
  expect(await screen.findByText('****7A2F')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建连接器' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /轮换/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole('tab', { name: '外部告警研判' }));
  await screen.findByText('Prometheus CPU 告警');
  expect(screen.queryByRole('button', { name: /研判 Prometheus/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /推进 Prometheus/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /转为故障 Prometheus/ })).not.toBeInTheDocument();
}, 30_000);
