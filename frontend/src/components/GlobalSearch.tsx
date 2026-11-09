import { useCallback, useEffect, useRef, useState } from 'react';
import { Empty, Input, List, Modal, Spin, Tag } from 'antd';
import { Boxes, FileWarning, Search, Server } from 'lucide-react';

import { getAssets } from '../api/assets';
import { getCmdbTree } from '../api/cmdb';
import { getVulnerabilities } from '../api/vulnerabilities';
import type { PageKey } from '../App';
import type { Asset } from '../types/asset';
import type { CmdbServiceTree } from '../types/cmdb';
import type { VulnerabilityFinding } from '../types/vulnerability';

export type SearchTarget =
  | { page: 'assets'; assetId: string; query: string }
  | { page: 'cmdb'; serviceId: string }
  | { page: 'findings'; assetId: string; query: string };

type SearchResult = {
  key: string;
  kind: 'asset' | 'service' | 'vulnerability';
  title: string;
  detail: string;
  target: SearchTarget;
};

const pageShortcuts: Array<{ page: PageKey; label: string; detail: string }> = [
  { page: 'dashboard', label: '综合总览', detail: '运维工作台' },
  { page: 'alerts', label: '活动告警', detail: '监控告警' },
  { page: 'scans', label: '漏洞扫描', detail: '安全中心' },
  { page: 'ops-assistant', label: 'AI 运维助手', detail: '只读排障对话' },
];

function flattenServices(tree: Awaited<ReturnType<typeof getCmdbTree>>): CmdbServiceTree[] {
  return tree.businesses.flatMap((business) => business.applications.flatMap((application) => application.services));
}

function assetResults(items: Asset[]): SearchResult[] {
  return items.slice(0, 8).map((item) => ({
    key: `asset:${item.id}`,
    kind: 'asset',
    title: item.name,
    detail: `${item.host} · ${item.group_name}`,
    target: { page: 'assets', assetId: item.id, query: item.host },
  }));
}

function serviceResults(items: CmdbServiceTree[]): SearchResult[] {
  return items.slice(0, 8).map((item) => ({
    key: `service:${item.id}`,
    kind: 'service',
    title: item.name,
    detail: `${item.code} · ${item.service_type}`,
    target: { page: 'cmdb', serviceId: item.id },
  }));
}

function vulnerabilityResults(items: VulnerabilityFinding[]): SearchResult[] {
  return items.slice(0, 8).map((item) => ({
    key: `vulnerability:${item.id}`,
    kind: 'vulnerability',
    title: item.cve_id,
    detail: `${item.asset_name} · ${item.package_name}`,
    target: { page: 'findings', assetId: item.asset_id, query: item.cve_id },
  }));
}

function iconFor(kind: SearchResult['kind']) {
  if (kind === 'asset') return <Server size={17} />;
  if (kind === 'service') return <Boxes size={17} />;
  return <FileWarning size={17} />;
}

export function GlobalSearch({ onNavigate, onSearchNavigate }: {
  onNavigate: (page: PageKey) => void;
  onSearchNavigate?: (target: SearchTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [shortcut, setShortcut] = useState('Ctrl K');
  const [searched, setSearched] = useState(false);
  const requestId = useRef(0);
  const close = useCallback(() => {
    requestId.current += 1;
    setOpen(false); setQuery(''); setResults([]); setLoading(false); setError(''); setSearched(false);
  }, []);

  useEffect(() => {
    const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
    setShortcut(isMac ? '⌘ K' : 'Ctrl K');
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
      }
      if (event.key === 'Escape') close();
    };
    const invalidate = () => close();
    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('blur', invalidate);
    window.addEventListener('sentinel:session-expired', invalidate);
    return () => {
      requestId.current += 1;
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('blur', invalidate);
      window.removeEventListener('sentinel:session-expired', invalidate);
    };
  }, [close]);

  const changeQuery = (value: string) => {
    requestId.current += 1;
    setQuery(value); setResults([]); setLoading(false); setError(''); setSearched(false);
  };

  const search = async (value: string) => {
    const normalized = value.trim();
    changeQuery(value);
    if (normalized.length < 2) {
      if (normalized) setError('请输入至少 2 个字符');
      return;
    }
    const currentRequest = requestId.current;
    setLoading(true);
    try {
      const [assets, tree, vulnerabilities] = await Promise.all([
        getAssets('active'),
        getCmdbTree('active', normalized),
        getVulnerabilities({ query: normalized, disposition: 'all', page: 1, pageSize: 8 }),
      ]);
      if (currentRequest !== requestId.current) return;
      const matches = (value: string) => value.toLowerCase().includes(normalized.toLowerCase());
      const assetMatches = assets.filter((item) => matches(`${item.name} ${item.host} ${item.group_name} ${item.labels.join(' ')}`));
      const serviceMatches = flattenServices(tree).filter((item) => matches(`${item.name} ${item.code} ${item.description ?? ''}`));
      setResults([
        ...assetResults(assetMatches),
        ...serviceResults(serviceMatches),
        ...vulnerabilityResults(vulnerabilities.items),
      ]);
      setSearched(true);
    } catch (value) {
      if (currentRequest !== requestId.current) return;
      setResults([]);
      setError(value instanceof Error ? value.message : '搜索暂时不可用');
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  };

  const shortcuts = pageShortcuts.filter((item) => !query.trim()
    || `${item.label} ${item.detail}`.toLowerCase().includes(query.trim().toLowerCase()));
  const select = (page: PageKey) => {
    close();
    onNavigate(page);
  };
  const selectResult = (item: SearchResult) => {
    close();
    if (onSearchNavigate) onSearchNavigate(item.target);
    else onNavigate(item.target.page);
  };

  return <>
    <button type="button" className="topbar-search" aria-label="打开全局搜索" onClick={() => setOpen(true)}>
      <Search size={15} />
      <span>搜索资产、服务或漏洞</span>
      <kbd>{shortcut}</kbd>
    </button>
    <Modal title="全局搜索" open={open} onCancel={close} footer={null} destroyOnHidden width={620}>
      <Input.Search
        autoFocus
        allowClear
        value={query}
        placeholder="输入资产、服务、CVE 或功能名称"
        enterButton="搜索"
        onChange={(event) => changeQuery(event.target.value)}
        onSearch={(value) => void search(value)}
      />
      <div className="global-search-results">
        {error ? <p className="global-search-error" role="alert">{error}</p> : null}
        {loading ? <div role="status" aria-label="正在搜索"><Spin /></div> : null}
        {!loading && !error && (!query.trim() || searched) && shortcuts.length ? <List
          header={query.trim() ? '功能入口' : '常用入口'}
          dataSource={shortcuts}
          renderItem={(item) => <List.Item>
            <button type="button" className="global-search-item" onClick={() => select(item.page)}>
              <Search size={17} aria-hidden="true" />
              <span className="global-search-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
            </button>
          </List.Item>}
        /> : null}
        {!loading && query.trim() && results.length ? <List
          header={`搜索结果（${results.length}）`}
          dataSource={results}
          renderItem={(item) => <List.Item>
            <button type="button" className="global-search-item" onClick={() => selectResult(item)}>
              <span aria-hidden="true">{iconFor(item.kind)}</span>
              <span className="global-search-copy"><strong>{item.title}</strong><small>{item.detail}</small></span>
              <Tag>{item.kind === 'asset' ? '资产' : item.kind === 'service' ? '服务' : '漏洞'}</Tag>
            </button>
          </List.Item>}
        /> : null}
        {!loading && searched && !results.length && !shortcuts.length && !error
          ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配结果" /> : null}
      </div>
    </Modal>
  </>;
}
