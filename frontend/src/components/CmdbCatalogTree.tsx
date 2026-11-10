import { useMemo, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { Button, Empty, Space } from 'antd';
import { Boxes, Building2, ChevronDown, ChevronRight, Layers, ListTree, Plug } from 'lucide-react';

import type { CmdbBusinessTree, CmdbEntityRow } from '../types/cmdb';

const typeLabels = { business: '业务系统', application: '应用', service: '服务', api: 'API' };
const lifecycleLabels = { active: '运行中', disabled: '已停用', archived: '已归档' };
const icons = { business: Building2, application: Layers, service: Boxes, api: Plug };

export function flattenCmdbTree(businesses: CmdbBusinessTree[]): CmdbEntityRow[] {
  const rows: CmdbEntityRow[] = [];
  businesses.forEach((business) => {
    rows.push({ key: `business-${business.id}`, type: 'business', entity: business, business, depth: 0 });
    business.applications.forEach((application) => {
      rows.push({ key: `application-${application.id}`, type: 'application', entity: application, business, application, depth: 1 });
      application.services.forEach((service) => {
        rows.push({ key: `service-${service.id}`, type: 'service', entity: service, business, application, service, depth: 2 });
        service.apis.forEach((api) => rows.push({ key: `api-${api.id}`, type: 'api', entity: api, business, application, service, api, depth: 3 }));
      });
    });
  });
  return rows;
}

function ancestorKeys(row: CmdbEntityRow): string[] {
  return [
    ...(row.depth > 0 ? [`business-${row.business.id}`] : []),
    ...(row.depth > 1 && row.application ? [`application-${row.application.id}`] : []),
    ...(row.depth > 2 && row.service ? [`service-${row.service.id}`] : []),
  ];
}

interface Props {
  rows: CmdbEntityRow[];
  matches: CmdbEntityRow[];
  selectedKey?: string;
  onSelect: (row: CmdbEntityRow) => void;
  renderActions: (row: CmdbEntityRow) => ReactNode;
  loading?: boolean;
  emptyText?: string;
}

export function CmdbCatalogTree({ rows, matches, selectedKey, onSelect, renderActions, loading, emptyText }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const itemRefs = useRef(new Map<string, HTMLDivElement>());
  const directKeys = useMemo(() => new Set(matches.map((row) => row.key)), [matches]);
  const { visibleRows, children } = useMemo(() => {
    const included = new Set(matches.flatMap((row) => [...ancestorKeys(row), row.key]));
    const visibleRows = rows.filter((row) => included.has(row.key));
    const children = new Map<string, CmdbEntityRow[]>();
    visibleRows.forEach((row) => {
      const parent = ancestorKeys(row).at(-1) ?? '';
      children.set(parent, [...(children.get(parent) ?? []), row]);
    });
    return { visibleRows, children };
  }, [matches, rows]);
  const displayedRows = visibleRows.filter((row) => !ancestorKeys(row).some((key) => collapsed.has(key)));
  const toggle = (key: string) => setCollapsed((previous) => {
    const next = new Set(previous);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const handleKey = (event: KeyboardEvent<HTMLDivElement>, row: CmdbEntityRow) => {
    if (event.target !== event.currentTarget) return;
    const branch = children.get(row.key) ?? [];
    const open = branch.length > 0 && !collapsed.has(row.key);
    const index = displayedRows.findIndex((item) => item.key === row.key);
    let focusKey: string | undefined;
    switch (event.key) {
      case 'ArrowLeft': if (open) toggle(row.key); else focusKey = ancestorKeys(row).at(-1); break;
      case 'ArrowRight': if (branch.length && !open) toggle(row.key); else focusKey = branch[0]?.key; break;
      case 'ArrowDown': focusKey = displayedRows[index + 1]?.key; break;
      case 'ArrowUp': focusKey = displayedRows[index - 1]?.key; break;
      case 'Home': focusKey = displayedRows[0]?.key; break;
      case 'End': focusKey = displayedRows.at(-1)?.key; break;
      case 'Enter': case ' ': onSelect(row); break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (focusKey) itemRefs.current.get(focusKey)?.focus();
  };
  const renderBranch = (row: CmdbEntityRow): ReactNode => {
    const branch = children.get(row.key) ?? [];
    const open = !collapsed.has(row.key);
    const Icon = icons[row.type];
    const assetCount = row.service?.assets.filter((item) => item.status === 'active').length ?? 0;
    return <div key={row.key} role="treeitem" aria-label={`${typeLabels[row.type]} ${row.entity.name}`}
      aria-level={row.depth + 1} aria-selected={selectedKey === row.key} aria-expanded={branch.length ? open : undefined}
      tabIndex={0} className="cmdb-catalog-item" ref={(element) => { if (element) itemRefs.current.set(row.key, element); else itemRefs.current.delete(row.key); }}
      onKeyDown={(event) => handleKey(event, row)}>
      <div className={`cmdb-catalog-row${selectedKey === row.key ? ' is-selected' : ''}`} style={{ '--cmdb-depth': row.depth } as CSSProperties} onClick={() => onSelect(row)}>
        <div className="cmdb-catalog-identity">
          {branch.length ? <button type="button" className="cmdb-catalog-expander" aria-label={`${open ? '收起' : '展开'}${typeLabels[row.type]} ${row.entity.name}`} aria-expanded={open}
            onClick={(event) => { event.stopPropagation(); toggle(row.key); }}>{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button> : <span className="cmdb-catalog-expander-space" aria-hidden="true" />}
          <span className="cmdb-catalog-icon" aria-hidden="true"><Icon size={16} /></span>
          <button type="button" className="cmdb-catalog-select" aria-label={`选择${typeLabels[row.type]} ${row.entity.name}`} onClick={(event) => { event.stopPropagation(); onSelect(row); }}>
            <strong>{row.entity.name}</strong><small>{row.entity.code}</small>
            {row.type === 'service' ? <small>{row.service?.service_type} · {assetCount} 台服务器</small> : null}
            {row.type === 'api' ? <small className="cmdb-catalog-endpoint">{row.api?.http_method} {row.api?.path}</small> : null}
          </button>
        </div>
        <span className="cmdb-catalog-type">{typeLabels[row.type]}</span>
        <span className={`cmdb-catalog-state is-${row.entity.lifecycle_status}`}><i aria-hidden="true" />{lifecycleLabels[row.entity.lifecycle_status]}</span>
        <div className="cmdb-catalog-actions" onClick={(event) => event.stopPropagation()}>{directKeys.has(row.key) ? renderActions(row) : null}</div>
      </div>
      {branch.length && open ? <div role="group">{branch.map(renderBranch)}</div> : null}
    </div>;
  };
  return <section className="cmdb-catalog" aria-label="业务关系目录区域" aria-busy={loading}>
    <header className="cmdb-catalog-heading"><div><h2><ListTree size={17} aria-hidden="true" />业务关系目录</h2><span aria-live="polite">{loading ? '正在加载目录' : `${displayedRows.length} 个可见节点 · ${matches.length} 条匹配记录`}</span></div>
      <Space size={4} wrap><Button size="small" type="text" disabled={loading || !visibleRows.length} onClick={() => setCollapsed(new Set())}>全部展开</Button>
        <Button size="small" type="text" disabled={loading || !visibleRows.length} onClick={() => setCollapsed(new Set(rows.map((row) => row.key)))}>全部收起</Button></Space>
    </header>
    <div className="cmdb-catalog-columns" aria-hidden="true"><span>名称 / 编码</span><span>类型</span><span>生命周期</span><span>操作</span></div>
    {loading ? <div className="cmdb-tree-loading" role="status">正在加载配置项目录…</div> : <div role="tree" aria-label="业务关系目录">
      {visibleRows.length ? (children.get('') ?? []).map(renderBranch) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText ?? '暂无符合筛选条件的配置项'} />}
    </div>}
  </section>;
}
