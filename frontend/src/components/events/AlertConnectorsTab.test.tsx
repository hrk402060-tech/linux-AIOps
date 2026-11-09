import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AlertConnectorsTab } from './AlertConnectorsTab';

const connector = {
  id: 'connector-1',
  name: '生产告警',
  source_type: 'prometheus',
  status: 'active' as const,
  secret_masked: '********',
  operator: 'admin',
  created_at: '2026-10-19T00:00:00Z',
  updated_at: '2026-10-19T00:00:00Z',
  rotated_at: null,
};

const preflight = {
  read_only: true,
  configuration_ready: true,
  runtime_verified: false,
  checks: [
    { key: 'payload', status: 'passed', message: '样例格式有效' },
    { key: 'connector', status: 'passed', message: '连接器配置可用' },
    { key: 'asset', status: 'unverified', message: '未使用真实资产' },
    { key: 'authorization', status: 'unverified', message: '未验证真实服务器权限' },
    { key: 'notifications', status: 'unverified', message: '未验证邮件收件' },
    { key: 'runtime', status: 'unverified', message: '未接收真实告警' },
  ],
  asset: null,
};

const response = (body: unknown, status = 200) => Promise.resolve({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

function stubApi(preflightBody: unknown = preflight, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const route = `${init?.method ?? 'GET'} ${url.pathname}`;
    if (route === 'GET /api/v1/alert-connectors') return response([connector]);
    if (route === 'POST /api/v1/alert-connectors/connector-1/preflight') return response(preflightBody, status);
    if (route === 'POST /api/v1/alert-connectors/connector-1/test-request') return response({
      method: 'POST',
      path: '/api/v1/alert-ingest/connector-1',
      signature_algorithm: 'HMAC-SHA256',
      replay_window_seconds: 300,
      headers: {
        'Content-Type': 'application/json',
        'X-Alert-Timestamp': '1760000000',
        'X-Alert-Nonce': 'nonce-test-request-1234',
        'X-Alert-Signature': 'sha256=signature',
      },
      body: '{"external_id":"test-1"}',
      warning: '仅用于验证签名，不会写入告警或触发诊断、通知。',
    });
    throw new Error(`Unexpected connector API: ${route}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function show(role: 'admin' | 'operator' | 'auditor') {
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AlertConnectorsTab currentUser={{ role }} />
    </ConfigProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AlertConnectorsTab 接入预检', () => {
  it('仅管理员显示接入预检入口并提供安全默认样例', async () => {
    stubApi();
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    expect(await screen.findByRole('dialog', { name: '接入预检：生产告警' })).toBeInTheDocument();
    const sampleInput = screen.getByLabelText('预检样例 JSON') as HTMLTextAreaElement;
    expect(sampleInput.value).toContain('DEMO-preflight');
    expect(sampleInput.value).toContain('192.0.2.10');
    expect(screen.getByText(/不会发送真实告警或邮件/)).toBeInTheDocument();
  });

  it.each(['operator', 'auditor'] as const)('非管理员 %s 不显示预检按钮', async (role) => {
    stubApi();
    show(role);
    await screen.findByText('生产告警');
    expect(screen.queryByRole('button', { name: '接入预检 生产告警' })).not.toBeInTheDocument();
  });

  it('提交样例 payload 并展示六项状态，不泄露密钥或原始错误', async () => {
    const fetchMock = stubApi();
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/alert-connectors/connector-1/preflight',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('DEMO-preflight') }),
    ));
    const requestBody = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body;
    const parsedBody = JSON.parse(String(requestBody)) as { sample_payload: string };
    expect(typeof parsedBody.sample_payload).toBe('string');
    expect(JSON.parse(parsedBody.sample_payload)).toMatchObject({
      receiver: 'ops',
      status: 'firing',
      alerts: [{
        fingerprint: 'DEMO-preflight',
        labels: { instance: '192.0.2.10', severity: 'medium' },
        annotations: { summary: '示例告警（预检）' },
      }],
    });
    expect(await screen.findByText('预检完成，未发送告警/邮件')).toBeInTheDocument();
    expect(screen.getByText('样例格式')).toBeInTheDocument();
    expect(screen.getByText('只读授权配置')).toBeInTheDocument();
    expect(screen.getByText('连接器配置')).toBeInTheDocument();
    expect(screen.getByText('资产绑定')).toBeInTheDocument();
    expect(screen.getByText('通知配置')).toBeInTheDocument();
    expect(screen.getByText('运行链路')).toBeInTheDocument();
    expect(screen.getByText('样例格式有效')).toBeInTheDocument();
    expect(screen.getByText('未接收真实告警')).toBeInTheDocument();
    const dialog = screen.getByRole('dialog', { name: '接入预检：生产告警' });
    expect(dialog).not.toHaveTextContent('********');
    expect(dialog).not.toHaveTextContent(/Traceback|secret|api_key/i);
  });

  it('显示未配置提示且不原样展示服务端错误', async () => {
    stubApi({ detail: 'internal secret=do-not-show' }, 409);
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    expect(await screen.findByText('接入预检暂不可用，请先完成连接器配置')).toBeInTheDocument();
    expect(screen.queryByText(/do-not-show|secret=/)).not.toBeInTheDocument();
  });

  it('拒绝无效 JSON，不发送预检请求', async () => {
    const fetchMock = stubApi();
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.change(screen.getByLabelText('预检样例 JSON'), { target: { value: '[' } });
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    expect(await screen.findByText('样例 JSON 格式不正确，请修正后重试')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
  });

  it('样例变更时清除旧结果，关闭再打开恢复默认样例', async () => {
    stubApi();
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    await screen.findByText('预检完成，未发送告警/邮件');
    fireEvent.change(screen.getByLabelText('预检样例 JSON'), { target: { value: '{"external_id":"changed"}' } });
    expect(screen.queryByText('预检完成，未发送告警/邮件')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: '接入预检 生产告警' }));
    expect((await screen.findByLabelText('预检样例 JSON') as HTMLTextAreaElement).value)
      .toContain('DEMO-preflight');
    expect(screen.queryByText('预检完成，未发送告警/邮件')).not.toBeInTheDocument();
  }, 20_000);

  it('关闭重开后忽略前一次迟到响应', async () => {
    const fetchMock = stubApi();
    const normal = fetchMock.getMockImplementation()!;
    let finish: ((value: Awaited<ReturnType<typeof response>>) => void) | undefined;
    fetchMock.mockImplementation((input, init) => init?.method === 'POST'
      ? new Promise((resolve) => { finish = resolve; })
      : normal(input, init));
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    await waitFor(() => expect(finish).toBeDefined(), { timeout: 15_000 });
    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: '接入预检 生产告警' }));
    await act(async () => { finish?.(await response(preflight)); });
    await waitFor(() => expect(screen.getByRole('button', { name: '运行预检' })).toBeEnabled(), { timeout: 15_000 });
    expect(screen.queryByText('预检完成，未发送告警/邮件')).not.toBeInTheDocument();
  }, 20_000);

  it('配置不完整展示阻断状态，永不把服务端消息当作可见错误', async () => {
    stubApi({
      ...preflight,
      configuration_ready: false,
      checks: preflight.checks.map((check) => check.key === 'notifications'
        ? { ...check, status: 'blocked', message: 'secret=private-mail-password Traceback' } : check),
    });
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    fireEvent.click(screen.getByRole('button', { name: '运行预检' }));
    expect(await screen.findByText(/配置预检未通过/)).toBeInTheDocument();
    expect(screen.getByText('未通过')).toBeInTheDocument();
    expect(screen.queryByText(/private-mail-password|Traceback/)).not.toBeInTheDocument();
  });

  it('按 Zabbix 来源填充可被后端标准化的样例', async () => {
    const zabbixConnector = { ...connector, source_type: 'zabbix' };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost');
      const route = `${init?.method ?? 'GET'} ${url.pathname}`;
      if (route === 'GET /api/v1/alert-connectors') return response([zabbixConnector]);
      if (route === 'POST /api/v1/alert-connectors/connector-1/preflight') return response(preflight);
      throw new Error(`Unexpected connector API: ${route}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入预检 生产告警' }));
    const sample = JSON.parse(
      (await screen.findByLabelText('预检样例 JSON') as HTMLTextAreaElement).value,
    ) as { eventid: string; host: { host: string }; severity: string };
    expect(sample).toMatchObject({
      eventid: 'DEMO-preflight',
      host: { host: '192.0.2.10' },
      severity: 'Warning',
    });
  });

  it('展示接入向导并生成不落库的签名测试请求', async () => {
    const fetchMock = stubApi();
    show('admin');
    fireEvent.click(await screen.findByRole('button', { name: '接入说明 生产告警' }));
    expect(await screen.findByRole('dialog', { name: '告警接入向导：生产告警' })).toBeInTheDocument();
    expect(screen.getByText('HMAC-SHA256')).toBeInTheDocument();
    expect(screen.getByText('X-Alert-Timestamp')).toBeInTheDocument();
    expect(screen.getByText(/时间戳与 nonce 必须在重放窗口内唯一/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '生成测试请求' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/alert-connectors/connector-1/test-request',
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByText('测试请求已生成')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/sha256=signature/)).toBeInTheDocument();
    expect(screen.getByText(/不会写入告警或触发诊断、通知/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/secret|密钥明文/i)).not.toBeInTheDocument();
  });
});
