import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AssetsPage } from './AssetsPage';

vi.setConfig({ testTimeout: 15_000 });

const asset = {
  id: 'asset-1',
  name: 'openEuler 核心数据库',
  host: '10.20.0.15',
  ssh_port: 22,
  os_family: 'openeuler' as const,
  environment: 'production' as const,
  group_name: '核心业务',
  credential_id: null,
  credential: null,
  labels: ['database'],
  status: 'unknown' as const,
  lifecycle_status: 'enabled' as const, lifecycle_reason: null,
  lifecycle_operator: null, archived_at: null,
  latency_ms: null,
  check_message: null,
  last_checked_at: null,
  created_at: '2026-09-03T08:00:00Z',
  updated_at: '2026-09-03T08:00:00Z',
};

function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const cleanupPreview = { asset_id: asset.id, allowed: true, reason: null,
  confirmation_phrase: 'DELETE asset-1', scan_tasks: 0, shared_scan_tasks: 0,
  vulnerability_findings: 0, vulnerability_cases: 0, reports: 0,
  monitoring_records: 0, terminal_sessions: 0, agent_recommendations: 0 };
async function showCleanup(overrides: Partial<typeof cleanupPreview> = {}) {
  const fetchMock = vi.fn().mockResolvedValueOnce(response([{ ...asset, environment: 'test' }]))
    .mockResolvedValueOnce(response({ ...cleanupPreview, ...overrides }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><AssetsPage /></ConfigProvider>);
  await screen.findByText(asset.name);
  fireEvent.click(screen.getByRole('button', { name: `清理 ${asset.name} 测试数据` }));
  const dialog = within(await screen.findByRole('dialog')); 
  await dialog.findByText('清理测试资产', { selector: 'strong' });
  return { dialog, fetchMock };
}

describe('AssetsPage', () => {
  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response([asset])));
  });

  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('focuses the selected global-search asset and clears that focus when editing its search', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response([
      asset, { ...asset, id: 'asset-2', name: '另一台服务器' },
    ])));
    render(<ConfigProvider><AssetsPage initialAssetId="asset-1" initialQuery={asset.host} /></ConfigProvider>);
    await screen.findByText(asset.name);
    expect(screen.queryByText('另一台服务器')).not.toBeInTheDocument();
    const search = screen.getByPlaceholderText('搜索名称、地址、分组或标签');
    expect(search).toHaveValue(asset.host);
    fireEvent.change(search, { target: { value: '' } });
    expect(screen.getByText('另一台服务器')).toBeInTheDocument();
  });

  it('shows Linux assets and domestic distribution names', async () => {
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);

    expect(await screen.findByText('openEuler 核心数据库')).toBeInTheDocument();
    expect(screen.getByText('openEuler')).toBeInTheDocument();
    expect(screen.getByText('openEuler · 生产 · 核心业务 · SSH 未配置')).toBeInTheDocument();
    expect(screen.getAllByText('待检查')).toHaveLength(2);
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(['/api/v1/assets']);
  });

  it('opens direct SSH login controls without a credential selector', async () => {
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');

    await userEvent.click(screen.getByRole('button', { name: '新建资产' }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByLabelText('资产名称')).toBeInTheDocument();
    expect(within(drawer).getByLabelText('主机地址')).toBeInTheDocument();
    expect(within(drawer).getByLabelText('操作系统')).toBeInTheDocument();
    expect(within(drawer).getByLabelText('SSH 用户名')).toBeInTheDocument();
    expect(within(drawer).getByText('SSH 认证方式', { selector: 'label' })).toBeInTheDocument();
    expect(within(drawer).getByText('新增资产必填；编辑时留空保留原认证')).toBeInTheDocument();
    expect(within(drawer).getByText('密码', { selector: '.ant-select-selection-item' })).toBeInTheDocument();
    expect(within(drawer).getByLabelText('SSH 密码')).toBeInTheDocument();
    expect(within(drawer).queryByLabelText('连接凭据')).not.toBeInTheDocument();
  });

  it('submits inline SSH credentials with a new asset', async () => {
    const created = {
      ...asset,
      id: 'asset-2',
      name: 'new-node',
      host: '10.20.0.16',
      credential_id: 'credential-2',
      credential: { id: 'credential-2', name: '资产 SSH', username: 'root', credential_type: 'password' },
      status: 'online' as const,
      check_message: 'SSH 登录成功',
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([asset]))
      .mockResolvedValueOnce(response(created, 201))
      .mockResolvedValueOnce(response([created]));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText(asset.name);
    await userEvent.click(screen.getByRole('button', { name: '新建资产' }));
    const drawer = await screen.findByRole('dialog');
    await userEvent.type(within(drawer).getByLabelText('资产名称'), 'new-node');
    await userEvent.type(within(drawer).getByLabelText('主机地址'), '10.20.0.16');
    await userEvent.type(within(drawer).getByLabelText('SSH 用户名'), 'root');
    await userEvent.type(within(drawer).getByLabelText('SSH 密码'), 'test-password');
    await userEvent.click(within(drawer).getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/assets'));
    expect(await screen.findByText('资产已创建并完成 SSH 登录检查')).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual(expect.objectContaining({
      host: '10.20.0.16',
      ssh_username: 'root',
      ssh_credential_type: 'password',
      ssh_secret: 'test-password',
      credential_id: null,
    }));
  }, 15_000);

  it('runs a read-only connectivity check and refreshes the row', async () => {
    const online = { ...asset, status: 'online', latency_ms: 9, check_message: 'SSH 端口可达' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([asset]))
      .mockResolvedValueOnce(response(online));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');

    await userEvent.click(screen.getByRole('button', { name: '检查 openEuler 核心数据库' }));

    await waitFor(() => expect(screen.getByText('在线')).toBeInTheDocument());
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/assets/asset-1/check');
  });

  it('does not expose a host key registration workflow', async () => {
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');
    expect(screen.queryByRole('button', { name: /登记 .*SSH (指纹|主机密钥)/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/主机指纹/)).not.toBeInTheDocument();
  });

  it('opens current findings for the selected asset', async () => {
    const onViewFindings = vi.fn();
    render(<ConfigProvider><AssetsPage onViewFindings={onViewFindings} /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');

    await userEvent.click(screen.getByRole('button', { name: '查看 openEuler 核心数据库 漏洞' }));

    expect(onViewFindings).toHaveBeenCalledWith('asset-1');
  });

  it('opens the terminal for an asset with a bound credential', async () => {
    const onOpenTerminal = vi.fn();
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response([{ ...asset, credential_id: 'credential-1' }])));
    render(<ConfigProvider><AssetsPage onOpenTerminal={onOpenTerminal} /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');
    await userEvent.click(screen.getByRole('button', { name: '打开 openEuler 核心数据库 终端' }));
    expect(onOpenTerminal).toHaveBeenCalledWith('asset-1');
  });

  it('archives an asset instead of physically deleting evidence', async () => {
    const archived = { ...asset, lifecycle_status: 'archived' as const, archived_at: '2026-09-08T08:00:00Z' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([asset]))
      .mockResolvedValueOnce(response(archived))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');

    await userEvent.click(screen.getByRole('button', { name: '归档 openEuler 核心数据库' }));

    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/assets/asset-1/lifecycle'));
  });

  it('requires dependency preview and exact confirmation for test cleanup', async () => {
    const testAsset = { ...asset, environment: 'test' as const };
    const preview = { asset_id: asset.id, allowed: true, reason: null,
      confirmation_phrase: 'DELETE asset-1', scan_tasks: 0, shared_scan_tasks: 0,
      vulnerability_findings: 0, vulnerability_cases: 0, reports: 0,
      monitoring_records: 0, terminal_sessions: 0, agent_recommendations: 0 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([testAsset]))
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response({ status: 'deleted', audit_id: 'audit-1' }))
      .mockResolvedValueOnce(response([]));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AssetsPage /></ConfigProvider>);
    await screen.findByText('openEuler 核心数据库');

    await userEvent.click(screen.getByRole('button', { name: '清理 openEuler 核心数据库 测试数据' }));
    const dialog = await screen.findByRole('dialog');
    const confirmButton = within(dialog).getByRole('button', { name: '确认清理' });
    expect(within(dialog).getByText('清理测试资产', { selector: 'strong' })).toBeInTheDocument();
    expect(within(dialog).getByText(asset.name)).toBeInTheDocument();
    expect(within(dialog).getByText(`${asset.host}:${asset.ssh_port}`)).toBeInTheDocument();
    expect(within(dialog).getByText('清理原因需 5 至 500 个字符，首尾空格不计入。')).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText('清理原因'), '清理自动化测试数据');
    await userEvent.type(within(dialog).getByLabelText('二次确认'), 'wrong');
    expect(confirmButton).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText('二次确认'));
    await userEvent.type(within(dialog).getByLabelText('二次确认'), '  清理测试资产  ');
    expect(confirmButton).toBeEnabled();
    await userEvent.click(confirmButton);

    await waitFor(() => expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/assets/asset-1/cleanup'));
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({
      body: JSON.stringify({ reason: '清理自动化测试数据', confirmation: 'DELETE asset-1' }),
    }));
  }, 30_000);
  it('explains short cleanup reasons, exact Chinese confirmation and the maximum', async () => {
    const { dialog, fetchMock } = await showCleanup();
    const confirm = dialog.getByRole('button', { name: '确认清理' });
    fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: '清理测试资产' } });
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '1' } });
    expect(confirm).toBeDisabled();
    expect(dialog.getByText('清理原因不足 5 个字符，请补充说明。')).toBeInTheDocument();
    expect(dialog.getByLabelText('清理原因')).toHaveAttribute('aria-invalid', 'true');
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: 'x'.repeat(501) } });
    expect(confirm).toBeDisabled();
    expect(dialog.getByText('清理原因不能超过 500 个字符。')).toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '  清理测试记录  ' } });
    fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: 'DELETE asset-1' } });
    expect(confirm).toBeDisabled();
    expect(dialog.getByText('确认文本不匹配，请输入“清理测试资产”。')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  }, 15_000);
  it.each([{ allowed: false }, { confirmation_phrase: '' }, { confirmation_phrase: 'DELETE other-asset' }, { asset_id: 'other-asset' }])(
    'does not confirm an invalid or rejected preview %j', async (overrides) => {
      const { dialog, fetchMock } = await showCleanup(overrides);
      fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '清理测试记录' } });
      fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: '清理测试资产' } });
      const confirm = dialog.getByRole('button', { name: '确认清理' });
      expect(confirm).toBeDisabled(); fireEvent.click(confirm);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  it('clears the human confirmation when cleanup is cancelled and reopened', async () => {
    const { dialog, fetchMock } = await showCleanup();
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '清理测试记录' } });
    fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: '清理测试资产' } });
    expect(dialog.getByRole('button', { name: '确认清理' })).toBeEnabled();
    fireEvent.click(dialog.getByRole('button', { name: /返\s*回/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fetchMock.mockResolvedValueOnce(response(cleanupPreview));
    fireEvent.click(screen.getByRole('button', { name: `清理 ${asset.name} 测试数据` }));
    const reopened = within(await screen.findByRole('dialog')); 
    expect(await reopened.findByLabelText('二次确认')).toHaveValue('');
    expect(reopened.getByLabelText('清理原因')).toHaveValue('');
    expect(reopened.getByRole('button', { name: '确认清理' })).toBeDisabled();
  });
  it('maps a human confirmation only once while cleanup is pending', async () => {
    const { dialog, fetchMock } = await showCleanup();
    let finish: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing request'); };
    fetchMock.mockReturnValueOnce(new Promise<ReturnType<typeof response>>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(response([]));
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '  清理测试记录  ' } });
    fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: '  清理测试资产  ' } });
    const confirm = dialog.getByRole('button', { name: '确认清理' });
    fireEvent.click(confirm); fireEvent.click(confirm);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({
      body: JSON.stringify({ reason: '清理测试记录', confirmation: 'DELETE asset-1' }),
    }));
    await act(async () => { finish(response({ status: 'deleted', audit_id: 'audit-1' })); });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it('ignores a late preview after switching cleanup targets', async () => {
    const other = { ...asset, id: 'asset-2', name: '另一测试服务器', environment: 'test' };
    let finish: (value: ReturnType<typeof response>) => void = () => { throw new Error('Missing preview'); };
    const fetchMock = vi.fn().mockResolvedValueOnce(response([{ ...asset, environment: 'test' }, other]))
      .mockReturnValueOnce(new Promise<ReturnType<typeof response>>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce(response({ ...cleanupPreview, asset_id: other.id, confirmation_phrase: 'DELETE asset-2' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><AssetsPage /></ConfigProvider>);
    fireEvent.click(await screen.findByRole('button', { name: `清理 ${asset.name} 测试数据` }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: /返\s*回/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: `清理 ${other.name} 测试数据` }));
    const dialog = within(await screen.findByRole('dialog'));
    await dialog.findByLabelText('清理原因');
    await act(async () => { finish(response(cleanupPreview)); });
    expect(dialog.getByText(other.name, { selector: 'strong' })).toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText('清理原因'), { target: { value: '清理测试记录' } });
    fireEvent.change(dialog.getByLabelText('二次确认'), { target: { value: '清理测试资产' } });
    expect(dialog.getByRole('button', { name: '确认清理' })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  }, 15_000);
});
