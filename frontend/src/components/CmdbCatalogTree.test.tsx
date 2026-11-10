import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { tree } from '../test/cmdbFixtures';
import { CmdbCatalogTree, flattenCmdbTree } from './CmdbCatalogTree';

function catalogRows() {
  const data = structuredClone(tree.businesses);
  const business = data[0];
  const application = business.applications[0];
  application.services.push({ ...application.services[0], id: 'service-2', name: '库存服务', code: 'STOCK', apis: [], assets: [] });
  business.applications.push({ ...application, id: 'application-2', name: '结算应用', code: 'SETTLE', services: [] });
  data.push({ ...business, id: 'business-2', name: '独立业务', code: 'OTHER', applications: [] });
  return flattenCmdbTree(data);
}

describe('CmdbCatalogTree', () => {
  it('collapses branches independently and hides all descendants of a collapsed business', async () => {
    const rows = catalogRows();
    render(<CmdbCatalogTree rows={rows} matches={rows} onSelect={vi.fn()} renderActions={() => null} />);
    const directory = screen.getByRole('tree', { name: '业务关系目录' });
    await userEvent.click(screen.getByRole('button', { name: '收起服务 支付 API' }));
    expect(within(directory).queryByText('创建订单')).not.toBeInTheDocument();
    expect(within(directory).getByText('库存服务')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '收起业务系统 支付业务' }));
    expect(within(directory).queryByText('支付网关')).not.toBeInTheDocument();
    expect(within(directory).queryByText('库存服务')).not.toBeInTheDocument();
    expect(within(directory).getByText('独立业务')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '展开业务系统 支付业务' }));
    expect(within(directory).getByText('支付网关')).toBeInTheDocument();
    expect(within(directory).queryByText('创建订单')).not.toBeInTheDocument();
  });

  it('supports expand all and collapse all without opening the detail panel', async () => {
    const onSelect = vi.fn();
    const rows = catalogRows();
    render(<CmdbCatalogTree rows={rows} matches={rows} onSelect={onSelect} renderActions={() => null} />);
    await userEvent.click(screen.getByRole('button', { name: '全部收起' }));
    expect(screen.getAllByRole('treeitem')).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: '全部展开' }));
    expect(screen.getAllByRole('treeitem')).toHaveLength(7);
    expect(screen.getByText('创建订单')).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('keeps search ancestors collapsible across rerenders and restores the prior branch state', async () => {
    const rows = catalogRows();
    const onSelect = vi.fn();
    const actions = () => null;
    const api = rows.find((row) => row.type === 'api')!;
    const { rerender } = render(<CmdbCatalogTree rows={rows} matches={[api]} onSelect={onSelect} renderActions={actions} />);
    expect(screen.getAllByRole('treeitem')).toHaveLength(4);
    expect(screen.queryByText('库存服务')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '收起业务系统 支付业务' }));
    rerender(<CmdbCatalogTree rows={rows} matches={[api]} onSelect={onSelect} renderActions={actions} />);
    expect(screen.getAllByRole('treeitem')).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: '展开业务系统 支付业务' }));
    expect(screen.getByText('创建订单')).toBeInTheDocument();
  });

  it('renders arrows only for visible branches and keeps arrow clicks separate from selection', async () => {
    const rows = catalogRows();
    const onSelect = vi.fn();
    render(<CmdbCatalogTree rows={rows} matches={rows} onSelect={onSelect} renderActions={() => null} />);
    for (const name of ['API 创建订单', '服务 库存服务', '应用 结算应用', '业务系统 独立业务']) {
      const item = screen.getByRole('treeitem', { name });
      expect(item).not.toHaveAttribute('aria-expanded');
      expect(within(item).queryByRole('button', { name: /展开|收起/ })).not.toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole('button', { name: '收起应用 支付网关' }));
    expect(onSelect).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '选择应用 支付网关' }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: 'application-application-1' }));
  });

  it('supports keyboard expansion, collapse and selecting a node', async () => {
    const rows = catalogRows();
    const onSelect = vi.fn();
    render(<CmdbCatalogTree rows={rows} matches={rows} onSelect={onSelect} renderActions={() => null} />);
    const root = screen.getByRole('treeitem', { name: '业务系统 支付业务' });
    root.focus();
    await userEvent.keyboard('{ArrowLeft}');
    expect(root).toHaveAttribute('aria-expanded', 'false');
    await userEvent.keyboard('{ArrowRight}');
    expect(root).toHaveAttribute('aria-expanded', 'true');
    await userEvent.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ type: 'business' }));
  });
});
