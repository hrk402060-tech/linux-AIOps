import { act, render, screen, within } from '@testing-library/react';
import userEvent, { PointerEventsCheckLevel } from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { assets, tree } from '../test/cmdbFixtures';
import type { CmdbServiceDependency } from '../types/cmdb';
import { flattenCmdbTree } from './CmdbCatalogTree';
import { CmdbDetailPanel } from './CmdbDetailPanel';

const actions = {
  onClose: vi.fn(), onEdit: vi.fn(),
  onBind: vi.fn(async () => undefined), onUnbind: vi.fn(async () => undefined), onRestore: vi.fn(async () => undefined),
};
const user = userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never });

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe('CmdbDetailPanel', () => {
  it('aggregates active descendant assets once for business and application nodes without relationship mutations', () => {
    const data = structuredClone(tree.businesses);
    const application = data[0].applications[0];
    const service = application.services[0];
    application.services.push({ ...service, id: 'service-2', name: '库存服务', apis: [] });
    service.assets.push({ ...service.assets[0], id: 'unlinked', asset_id: 'asset-2', asset_name: '历史节点', status: 'unlinked' });
    const rows = flattenCmdbTree(data);
    const services = application.services;
    const { rerender } = render(<CmdbDetailPanel row={rows[0]} services={services} assets={assets} canWrite {...actions} />);
    const related = screen.getByRole('region', { name: '关联服务器' });
    expect(within(related).getAllByText('生产节点')).toHaveLength(1);
    expect(within(related).queryByText('历史节点')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '绑定服务器' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /解绑|恢复关系/ })).not.toBeInTheDocument();
    rerender(<CmdbDetailPanel key={rows[1].key} row={rows[1]} services={services} assets={assets} canWrite {...actions} />);
    expect(screen.getByRole('complementary', { name: '应用详情' })).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: '关联服务器' })).getAllByText('生产节点')).toHaveLength(1);
  }, 20_000);

  it('does not put a delayed dependency response into the newly selected service', async () => {
    let resolveDependencies!: (value: { ok: boolean; status: number; json: () => Promise<CmdbServiceDependency[]> }) => void;
    const pending = new Promise<Parameters<typeof resolveDependencies>[0]>((resolve) => { resolveDependencies = resolve; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending));
    const data = structuredClone(tree.businesses);
    const application = data[0].applications[0];
    application.services.push({ ...application.services[0], id: 'service-2', name: '库存服务', apis: [], assets: [] });
    const rows = flattenCmdbTree(data).filter((row) => row.type === 'service');
    const { rerender } = render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbDetailPanel key={rows[0].key} row={rows[0]} services={application.services} assets={assets} canWrite {...actions} /></ConfigProvider>);
    await user.click(screen.getByRole('button', { name: '加载依赖' }));
    rerender(<ConfigProvider theme={{ token: { motion: false } }}><CmdbDetailPanel key={rows[1].key} row={rows[1]} services={application.services} assets={assets} canWrite {...actions} /></ConfigProvider>);
    await act(async () => resolveDependencies({ ok: true, status: 200, json: async () => [{
      id: 'dependency-1', source_service_id: 'service-1', source_service_name: '支付 API',
      target_service_id: 'old-target', target_service_name: '旧服务依赖', dependency_type: 'calls',
      description: null, status: 'active', created_at: '', updated_at: '',
    }] }));
    expect(screen.getByRole('heading', { name: '库存服务' })).toBeInTheDocument();
    expect(screen.queryByText(/旧服务依赖/)).not.toBeInTheDocument();
    expect(screen.getByText('依赖关系尚未加载')).toBeInTheDocument();
  }, 30_000);

  it('keeps service binding, unbinding and dependency operations available to writers', async () => {
    const rows = flattenCmdbTree(tree.businesses);
    const serviceRow = rows.find((row) => row.type === 'service')!;
    const nextAsset = { ...assets[0], id: 'asset-2', name: '备用节点', host: '192.0.2.20' };
    render(<ConfigProvider theme={{ token: { motion: false } }}><CmdbDetailPanel row={serviceRow} services={[serviceRow.service!]} assets={[...assets, nextAsset]} canWrite {...actions} /></ConfigProvider>);
    await user.click(screen.getByRole('button', { name: '绑定服务器' }));
    const dialog = screen.getByRole('dialog', { name: '绑定承载服务器' });
    await user.click(within(dialog).getByRole('combobox', { name: '服务器' }));
    await user.click(screen.getByText('备用节点 · 192.0.2.20'));
    await user.click(within(dialog).getByRole('button', { name: '确认绑定' }));
    await vi.waitFor(() => expect(actions.onBind).toHaveBeenCalledWith('service-1', 'asset-2', 'primary'));
    await user.click(screen.getByRole('button', { name: '解绑 生产节点' }));
    expect(actions.onUnbind).toHaveBeenCalledWith(serviceRow.service!.assets[0]);
    expect(screen.getByRole('button', { name: '添加依赖' })).toBeInTheDocument();
  }, 30_000);
});
