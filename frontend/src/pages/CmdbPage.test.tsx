import { render, screen, within } from '@testing-library/react';
import userEvent, { PointerEventsCheckLevel } from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { admin, assets, auditor, tree } from '../test/cmdbFixtures';
import { CmdbPage } from './CmdbPage';

const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never });
const discovery = {
  generated_at: '2026-11-07T00:00:00Z',
  source: 'latest_readonly_snapshot' as const,
  assets: [{
    asset_id: 'asset-1',
    asset_name: '生产节点',
    host: '192.0.2.10',
    observed_at: '2026-11-07T00:00:00Z',
    status: 'online',
    services: [{ name: 'nginx', source: 'systemd' as const, state: 'running' }],
  }],
};

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

afterEach(() => vi.unstubAllGlobals());

describe('CmdbPage', () => {
  it('loads service dependencies only when requested', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets))
      .mockResolvedValueOnce(response([{
        id: 'dependency-1',
        source_service_id: 'service-1',
        source_service_name: '支付 API',
        target_service_id: 'service-2',
        target_service_name: '库存服务',
        dependency_type: 'calls',
        description: '读取库存',
        status: 'active',
        created_at: '',
        updated_at: '',
      }]));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    await user.click(await screen.findByRole('button', { name: '查看服务 支付 API' }));
    const drawer = await screen.findByRole('complementary', { name: '服务详情' });
    expect(drawer).toBeVisible();
    await user.click(within(drawer).getByRole('button', { name: '加载依赖' }));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3), { timeout: 5_000 });
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/cmdb/services/service-1/dependencies');
    expect(await within(drawer).findByText('服务依赖')).toBeInTheDocument();
  }, 30_000);

  it('opens the read-only discovery preview on demand', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ businesses: [] }))
      .mockResolvedValueOnce(response(assets))
      .mockResolvedValueOnce(response(discovery));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    await user.click(await screen.findByRole('button', { name: '自动发现预览' }));

    expect(await screen.findByText('只读发现结果')).toBeInTheDocument();
    expect(screen.getByText('nginx · systemd')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/cmdb/discovery/preview',
      expect.objectContaining({ credentials: 'include' }),
    );
  }, 20_000);

  it('requires an application before confirming a discovered service', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets))
      .mockResolvedValueOnce(response(discovery))
      .mockResolvedValueOnce(response({ service_id: 'service-2', relation_id: 'relation-2', relation_status: 'active', created_service: true, restored_relation: false }))
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    await user.click(await screen.findByRole('button', { name: '自动发现预览' }));
    await user.click(await screen.findByRole('button', { name: 'nginx · systemd' }));
    const dialogs = await screen.findAllByRole('dialog');
    const dialog = dialogs[dialogs.length - 1];
    expect(within(dialog).getByRole('button', { name: '确认写入' })).toBeDisabled();
    await user.click(within(dialog).getByRole('combobox', { name: '目标应用' }));
    await user.click(await screen.findByText('支付网关', { selector: '.ant-select-item-option-content' }));
    await user.click(within(dialog).getByRole('button', { name: '确认写入' }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/cmdb/discovery/confirm',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({
        application_id: 'application-1', asset_id: 'asset-1', service_name: 'nginx', source: 'systemd',
      }) }),
    ));
  }, 30_000);

  it('applies the global-search service filter without hiding the service API relationships', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets)));
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} initialServiceId="service-1" /></ConfigProvider>);
    await screen.findByRole('button', { name: '查看服务 支付 API' });
    expect(screen.getByRole('button', { name: '查看API 创建订单' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看业务系统 支付业务' })).not.toBeInTheDocument();
    expect(screen.getByText('支付 API', { selector: '.ant-select-selection-item' })).toBeInTheDocument();
  }, 30_000);

  it('shows the business to API hierarchy and service asset detail', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets)));
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    expect(screen.getByRole('heading', { name: '系统/接口监控', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新系统/接口监控' })).toBeInTheDocument();
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(screen.queryByText('目录关系')).not.toBeInTheDocument();
    expect(screen.queryByText('服务器资产挂在“服务”下面；点击任意节点可查看详情和关联关系。')).not.toBeInTheDocument();
    expect((await screen.findAllByText('支付业务')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('支付网关').length).toBeGreaterThan(0);
    expect(screen.getAllByText('支付 API').length).toBeGreaterThan(0);
    expect(screen.getAllByText('创建订单').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: '查看服务 支付 API' }));
    const drawer = await screen.findByRole('complementary', { name: '服务详情' });
    expect(within(drawer).getByText('生产节点')).toBeInTheDocument();
    expect(within(drawer).getByText('192.0.2.10')).toBeInTheDocument();
  }, 30_000);

  it('closes inline details back to full width and reopens the selected API relationships', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets)));
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: '选择业务系统 支付业务' }));
    const drawer = screen.getByRole('complementary', { name: '业务系统详情' });
    expect(drawer).toBeVisible();
    expect(screen.getByRole('tree', { name: '业务关系目录' }).closest('.cmdb-catalog-layout')).toHaveClass('details-open');
    await user.click(within(drawer).getByRole('button', { name: '收起详情' }));
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(screen.getByRole('tree', { name: '业务关系目录' }).closest('.cmdb-catalog-layout')).not.toHaveClass('details-open');
    await user.click(screen.getByRole('button', { name: '选择API 创建订单' }));
    const apiDetail = screen.getByRole('complementary', { name: 'API详情' });
    expect(within(apiDetail).getByText('POST /orders')).toBeInTheDocument();
    expect(within(apiDetail).getByText('生产节点')).toBeInTheDocument();
    expect(within(apiDetail).queryByRole('button', { name: '绑定服务器' })).not.toBeInTheDocument();
  }, 45_000);

  it('allows an administrator to create a business', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ businesses: [] }))
      .mockResolvedValueOnce(response(assets))
      .mockResolvedValueOnce(response({ id: 'business-2' }, 201))
      .mockResolvedValueOnce(response({ businesses: [] }))
      .mockResolvedValueOnce(response(assets));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    await user.click(await screen.findByRole('button', { name: '新建业务系统' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('名称'), '订单业务');
    await user.type(within(dialog).getByLabelText('编码'), 'ORDER');
    await user.click(within(dialog).getByRole('button', { name: /保\s*存/ }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/cmdb/businesses', expect.objectContaining({ method: 'POST' })));
  }, 30_000);

  it('shows and restores an unlinked service asset relationship', async () => {
    const historyTree = structuredClone(tree);
    historyTree.businesses[0].applications[0].services[0].assets[0].status = 'unlinked';
    historyTree.businesses[0].applications[0].services[0].assets[0].unlinked_at = '2026-09-15T08:00:00Z';
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(historyTree))
      .mockResolvedValueOnce(response(assets))
      .mockResolvedValueOnce(response({ status: 'active' }))
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={admin} /></ConfigProvider>);

    await user.click(await screen.findByRole('button', { name: '查看服务 支付 API' }));
    const drawer = await screen.findByRole('complementary', { name: '服务详情' });
    expect(within(drawer).getByText('已解除')).toBeInTheDocument();
    await user.click(within(drawer).getByRole('button', { name: '恢复关系 生产节点' }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/cmdb/service-assets/relation-1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'active' }) }),
    ));
  }, 30_000);

  it('keeps auditors read-only', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(tree))
      .mockResolvedValueOnce(response(assets)));
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbPage currentUser={auditor} /></ConfigProvider>);
    expect((await screen.findAllByText('支付业务')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '新建业务系统' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('编辑支付业务')).not.toBeInTheDocument();
    expect(screen.getByText('审计员只读')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '选择服务 支付 API' }));
    const detail = screen.getByRole('complementary', { name: '服务详情' });
    expect(within(detail).queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
    expect(within(detail).queryByRole('button', { name: '绑定服务器' })).not.toBeInTheDocument();
  }, 30_000);
});
