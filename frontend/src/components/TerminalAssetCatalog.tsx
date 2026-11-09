import { useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Pagination, Spin, Tooltip, Tree } from 'antd';
import { ChevronDown, Folder, Monitor, RefreshCw, Search, Server } from 'lucide-react';

import type { Asset } from '../types/asset';
import { osLabels, statusLabels } from '../utils/assets';
import { terminalAssetHref } from '../utils/terminalNavigation';
import './TerminalAssetCatalog.css';

interface Props {
  assets: Asset[];
  loading: boolean;
  error: string;
  onReload: () => void;
  onOpenInPage: (assetId: string) => void;
}

export function TerminalAssetCatalog({ assets, loading, error, onReload, onOpenInPage }: Props) {
  const [group, setGroup] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const groups = useMemo(() => {
    const counts = new Map<string, number>();
    assets.forEach((item) => {
      const name = item.group_name || '未分组';
      counts.set(name, (counts.get(name) ?? 0) + 1);
    });
    return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right, 'zh-CN'));
  }, [assets]);
  const filtered = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return assets.filter((item) => (group === null || (item.group_name || '未分组') === group)
      && `${item.name} ${item.host} ${item.group_name} ${item.credential?.username ?? ''}`.toLocaleLowerCase().includes(keyword));
  }, [assets, group, query]);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(filtered.length / pageSize)));
  const rows = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const tree = [{
    key: 'all', title: `全部服务器 (${assets.length})`, icon: <Server size={16} />,
    children: groups.map(([name, count]) => ({ key: `group:${name}`, title: `${name} (${count})`, icon: <Folder size={16} /> })),
  }];
  return <div className="terminal-catalog">
    <aside className="terminal-catalog-tree" aria-label="资产树">
      <div className="terminal-catalog-tree-heading"><strong>资产树</strong><span>{assets.length}</span></div>
      <Tree aria-label="服务器分组" showIcon blockNode defaultExpandedKeys={['all']} treeData={tree}
        selectedKeys={[group === null ? 'all' : `group:${group}`]} switcherIcon={<ChevronDown size={14} />}
        onSelect={(keys) => {
          if (!keys.length) return;
          setGroup(keys[0] === 'all' ? null : String(keys[0]).slice('group:'.length)); setPage(1);
        }} />
    </aside>
    <div className="terminal-catalog-content">
      <div className="terminal-catalog-toolbar">
        <Input aria-label="搜索服务器" prefix={<Search size={16} />} allowClear placeholder="搜索名称、地址、分组或账号"
          value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} />
        <Tooltip title="刷新服务器资源"><Button aria-label="刷新服务器资源" icon={<RefreshCw size={17} />} loading={loading} onClick={onReload} /></Tooltip>
      </div>
      {error ? <Alert type="error" showIcon role="alert" message={error} /> : null}
      <Spin spinning={loading}>
        <div className="terminal-catalog-table-scroll">
          <table className="terminal-catalog-table" aria-label="SSH 服务器资源">
            <thead><tr><th>名称</th><th>地址</th><th>平台</th><th>账号</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>{rows.map((item) => {
              const configured = Boolean(item.credential_id);
              const path = terminalAssetHref(item.id);
              return <tr key={item.id}>
                <td><Tooltip title="在当前页打开"><button type="button" className="terminal-catalog-name" aria-label={`当前页打开 ${item.name}`}
                  disabled={!configured} onClick={() => onOpenInPage(item.id)}><Server size={16} /><span title={item.name}>{item.name}</span></button></Tooltip></td>
                <td><span title={`${item.host}:${item.ssh_port}`}>{item.host}:{item.ssh_port}</span></td>
                <td><span title={osLabels[item.os_family]}>{item.os_family === 'auto' ? 'Linux' : osLabels[item.os_family]}</span></td>
                <td><span>{item.credential?.username ?? '未配置'}</span></td>
                <td><span className="terminal-catalog-status"><i className={`terminal-asset-status is-${item.status}`} />{statusLabels[item.status]}</span></td>
                <td><Tooltip title={configured ? '新标签打开 SSH' : '请先在服务器资产配置 SSH 登录信息'}>
                  <Button type="primary" aria-label={`新标签连接 ${item.name}`} disabled={!configured}
                    href={configured ? path : undefined} target="_blank" rel="noopener noreferrer" icon={<Monitor size={17} />} />
                </Tooltip></td>
              </tr>;
            })}</tbody>
          </table>
          {!loading && !rows.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={query || group !== null ? '没有匹配的服务器' : '暂无启用的服务器'} /> : null}
        </div>
      </Spin>
      <div className="terminal-catalog-pagination"><span>共 {filtered.length} 台服务器</span>
        <Pagination size="small" current={currentPage} pageSize={pageSize} total={filtered.length} showSizeChanger pageSizeOptions={[15, 30, 50]}
          onChange={(next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size); }} />
      </div>
    </div>
  </div>;
}
