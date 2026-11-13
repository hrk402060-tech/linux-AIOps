import { act, render, screen, waitFor, within } from '@testing-library/react';
import { useEffect, useState } from 'react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { SearchTarget } from './components/GlobalSearch';
import type { ServiceMonitoringActions } from './components/ServiceMonitoringDetail';

const auth = vi.hoisted(() => ({
  user: { id: 'nav-user-a', username: 'nav-a', display_name: '用户甲',
    role: 'admin', enabled: true, must_change_password: false, access_revision: 1, created_at: '', last_login_at: null },
  getCurrentUser: vi.fn(), logout: vi.fn(), terminalUnmount: vi.fn(),
}));
vi.mock('./api/auth', async (importOriginal) => ({
  ...await importOriginal<typeof import('./api/auth')>(),
  getCurrentUser: auth.getCurrentUser, logout: auth.logout,
}));
vi.mock('./components/AccessGate', () => ({
  AccessGate: ({ onAuthenticated }: { onAuthenticated: (user: typeof auth.user) => void }) =>
    <button onClick={() => onAuthenticated({ ...auth.user, id: 'nav-user-b', username: 'nav-b', display_name: '用户乙' })}>测试登录</button>,
}));
vi.mock('./components/GlobalSearch', () => ({
  GlobalSearch: ({ onSearchNavigate }: { onSearchNavigate?: (target: SearchTarget) => void }) => {
    const [query, setQuery] = useState('');
    return <><input aria-label="测试搜索上下文" value={query} onChange={(event) => setQuery(event.target.value)} />
      <button onClick={() => onSearchNavigate?.({ page: 'assets', assetId: 'asset-search', query: '192.0.2.42' })}>搜索资产跳转</button>
      <button onClick={() => onSearchNavigate?.({ page: 'cmdb', serviceId: 'service-search' })}>搜索服务跳转</button>
      <button onClick={() => onSearchNavigate?.({ page: 'findings', assetId: 'asset-search', query: 'CVE-2026-1234' })}>搜索漏洞跳转</button>
    </>;
  },
}));
vi.mock('./pages/DashboardPage', () => ({
  DashboardPage: ({ onOpenTerminal, onViewFindings }: {
    onOpenTerminal?: (assetId: string) => void; onViewFindings?: (assetId: string) => void;
  }) => {
    const [context, setContext] = useState('');
    return <><p>总览测试页</p>
      <input aria-label="测试总览上下文" value={context} onChange={(event) => setContext(event.target.value)} />
      <button onClick={() => onOpenTerminal?.('asset-99')}>打开测试资产终端</button>
      <button onClick={() => onOpenTerminal?.('asset-100')}>打开另一个资产终端</button>
      <button onClick={() => onViewFindings?.('asset-99')}>查看测试资产漏洞</button>
    </>;
  },
}));
vi.mock('./pages/TerminalPage', () => ({
  TerminalPage: ({ initialAssetId }: { initialAssetId?: string | null }) => {
    const [context, setContext] = useState('');
    useEffect(() => () => auth.terminalUnmount(), []);
    return <><p>终端测试页{initialAssetId ? ` ${initialAssetId}` : ''}</p>
      <input aria-label="测试终端上下文" value={context} onChange={(event) => setContext(event.target.value)} /></>;
  },
}));
vi.mock('./pages/OperationsAssistantPage', () => ({ OperationsAssistantPage: () => <p>AI 运维助手测试页</p> }));
vi.mock('./pages/AssetsPage', () => ({
  AssetsPage: ({ onOpenTerminal, initialAssetId, initialQuery }: {
    onOpenTerminal?: (assetId: string) => void; initialAssetId?: string | null; initialQuery?: string;
  }) => {
    const [query, setQuery] = useState(initialQuery ?? '');
    return <><p>资产搜索上下文 {initialAssetId ?? '全部'} {initialQuery ?? ''}</p>
      <input aria-label="测试资产查询" value={query} onChange={(event) => setQuery(event.target.value)} />
      <button onClick={() => onOpenTerminal?.('asset-99')}>资产 SSH 跳转</button></>;
  },
}));
vi.mock('./pages/CmdbPage', () => ({
  CmdbPage: ({ initialServiceId }: { initialServiceId?: string }) =>
    <p>服务搜索上下文 {initialServiceId ?? '全部'}</p>,
}));
vi.mock('./pages/VulnerabilitiesPage', () => ({
  VulnerabilitiesPage: ({ initialAssetId, initialQuery, initialDisposition }: {
    initialAssetId?: string | null; initialQuery?: string; initialDisposition?: string;
  }) => <><p>漏洞测试页{initialAssetId ? ` ${initialAssetId}` : ''}</p>
    <p>漏洞搜索上下文 {initialQuery ?? ''} {initialDisposition ?? 'active'}</p></>,
}));
vi.mock('./pages/ReportsPage', () => ({ ReportsPage: () => <p>报告测试页</p> }));
vi.mock('./pages/SecuritySettingsPage', () => ({
  SecuritySettingsPage: ({ currentUser }: { currentUser: typeof auth.user }) =>
    <p>安全设置测试页 {currentUser.role}</p>,
}));
vi.mock('./pages/EventsPage', () => ({
  EventsPage: ({ onOpenTerminal, initialAssetId, initialTab, initialAlertId }: {
    onOpenTerminal?: (assetId: string) => void; initialAssetId?: string;
    initialTab?: string; initialAlertId?: string;
  }) => <><p>告警定位 {initialAssetId} {initialTab} {initialAlertId}</p>
    <button onClick={() => onOpenTerminal?.('alert-asset-88')}>告警资产 SSH 跳转</button></>,
}));
vi.mock('./pages/ObservabilityPage', () => ({
  ObservabilityPage: ({ onOpenAlerts }: ServiceMonitoringActions) =>
    <button onClick={() => onOpenAlerts?.('asset-service', 'external', 'alert-service')}>测试服务告警定位</button>,
}));

beforeEach(() => {
  localStorage.clear();
  auth.getCurrentUser.mockResolvedValue(auth.user);
  auth.logout.mockResolvedValue(undefined);
  auth.terminalUnmount.mockClear();
  vi.spyOn(window, 'open').mockReturnValue(null);
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
});

describe('使用文档独立页面', () => {
  it.each(['user-security', 'security-settings'])('security_navigation: 旧新入口 %s 都进入统一安全设置', async (page) => {
    window.history.replaceState({}, '', `/?page=${page}`);
    auth.getCurrentUser.mockResolvedValue({ ...auth.user, role: 'operator' });
    render(<App />);
    expect(await screen.findByText('安全设置测试页 operator')).toBeVisible();
    expect(document.querySelector('.topbar-context strong')).toHaveTextContent('安全设置');
    expect(screen.queryByRole('menuitem', { name: '个人安全' })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: '安全设置' })).toHaveClass('ant-menu-item-selected');
  });

  it('登录后展示完整文档，不挂载主侧栏或终端，相关功能另开安全标签', async () => {
    window.history.replaceState({}, '', '/?view=docs&terminal_asset=scope-node');
    let opened: { href: string; target: string; rel: string } | undefined;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      opened = { href: this.href, target: this.target, rel: this.rel };
    });
    render(<App />);
    await screen.findByRole('heading', { name: '使用文档', level: 1 });
    expect(screen.getByRole('heading', { name: '系统介绍' })).toBeVisible();
    expect(screen.getByRole('link', { name: '返回平台' })).toHaveAttribute('href', `${window.location.origin}/`);
    expect(screen.queryByRole('menu', { name: '主导航' })).not.toBeInTheDocument();
    expect(screen.queryByText(/终端测试页/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '打开相关功能' }));
    expect(opened).toEqual({ href: `${window.location.origin}/?page=dashboard`,
      target: '_blank', rel: 'noopener noreferrer' });
    expect(document.querySelector('a[hidden]')).toBeNull();
  });

  it('未登录时使用文档仍须登录，会话失效后清除文档', async () => {
    window.history.replaceState({}, '', '/?view=docs');
    auth.getCurrentUser.mockRejectedValueOnce(new Error('Unauthorized'));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: '测试登录' }));
    await screen.findByRole('heading', { name: '使用文档', level: 1 });
    act(() => window.dispatchEvent(new Event('sentinel:session-expired')));
    await screen.findByRole('button', { name: '测试登录' });
    expect(screen.queryByRole('heading', { name: '使用文档', level: 1 })).not.toBeInTheDocument();
  });

  it('使用文档不能跳过强制修改密码', async () => {
    window.history.replaceState({}, '', '/?view=docs');
    auth.getCurrentUser.mockResolvedValue({ ...auth.user, must_change_password: true });
    render(<App />);
    await screen.findByRole('heading', { name: '首次登录修改密码' });
    expect(screen.queryByRole('heading', { name: '使用文档', level: 1 })).not.toBeInTheDocument();
  });

  it.each([
    ['assets', '资产搜索上下文 全部'],
    ['dashboard', '总览测试页'],
    ['__proto__', '总览测试页'],
    ['credentials', '总览测试页'],
  ])('使用文档功能跳转 %s 只接受现有页面，显式总览优先于记忆', async (page, expected) => {
    window.history.replaceState({}, '', `/?page=${page}`);
    if (page === 'dashboard') {
      localStorage.setItem('sentinel_navigation:nav-user-a', JSON.stringify({ activePage: 'reports' }));
    }
    render(<App />);
    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.queryByText('报告测试页')).not.toBeInTheDocument();
  });
});

describe('App navigation authentication boundary', () => {
  it('alert_target: forwards the exact service alert and its asset into the existing alert page', async () => {
    window.history.replaceState({}, '', '/?page=observability');
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '测试服务告警定位' }));
    expect(await screen.findByText('告警定位 asset-service external alert-service')).toBeInTheDocument();
  });

  it.each([
    ['revision', { access_revision: 2 }],
    ['role', { role: 'operator' }],
    ['identity', { id: 'nav-user-other', username: 'nav-other' }],
  ])('nav_scope: %s changes clear mounted dashboard, search, and SSH fallback state', async (_, change) => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.type(await screen.findByRole('textbox', { name: '测试总览上下文' }), 'old-asset');
    await userEvent.type(screen.getByRole('textbox', { name: '测试搜索上下文' }), 'old-search');
    await userEvent.click(screen.getByRole('button', { name: '打开测试资产终端' }));
    await screen.findByRole('button', { name: '在当前页打开 SSH' });
    auth.getCurrentUser.mockResolvedValue({ ...auth.user, ...change });
    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(screen.getByRole('textbox', { name: '测试总览上下文' })).toHaveValue(''));
    expect(screen.getByRole('textbox', { name: '测试搜索上下文' })).toHaveValue('');
    expect(screen.queryByRole('button', { name: '在当前页打开 SSH' })).not.toBeInTheDocument();
  }, 15_000);

  it.each([
    ['revision', { access_revision: 2 }],
    ['role', { role: 'operator' }],
    ['identity', { id: 'nav-user-other', username: 'nav-other' }],
  ])('nav_scope: %s changes discard the standalone SSH connection context', async (_, change) => {
    window.history.replaceState({}, '', '/?terminal_asset=scope-node');
    render(<App />);
    await userEvent.type(await screen.findByRole('textbox', { name: '测试终端上下文' }), 'old-session');
    auth.getCurrentUser.mockResolvedValue({ ...auth.user, ...change });
    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(screen.getByRole('textbox', { name: '测试终端上下文' })).toHaveValue(''));
    expect(auth.terminalUnmount).toHaveBeenCalledTimes(1);
    expect(screen.getByText('终端测试页 scope-node')).toBeInTheDocument();
  });

  it('nav_scope: unchanged permissions preserve mounted dashboard and search state', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.type(await screen.findByRole('textbox', { name: '测试总览上下文' }), 'current-asset');
    await userEvent.type(screen.getByRole('textbox', { name: '测试搜索上下文' }), 'current-search');
    const requests = auth.getCurrentUser.mock.calls.length;
    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(auth.getCurrentUser).toHaveBeenCalledTimes(requests + 1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('textbox', { name: '测试总览上下文' })).toHaveValue('current-asset');
    expect(screen.getByRole('textbox', { name: '测试搜索上下文' })).toHaveValue('current-search');
  });

  it('nav_scope: unchanged permissions preserve the SSH connection context', async () => {
    window.history.replaceState({}, '', '/?terminal_asset=scope-node');
    render(<App />);
    await userEvent.type(await screen.findByRole('textbox', { name: '测试终端上下文' }), 'active-session');
    const requests = auth.getCurrentUser.mock.calls.length;
    act(() => window.dispatchEvent(new Event('focus')));
    await waitFor(() => expect(auth.getCurrentUser).toHaveBeenCalledTimes(requests + 1));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('textbox', { name: '测试终端上下文' })).toHaveValue('active-session');
    expect(auth.terminalUnmount).not.toHaveBeenCalled();
  });

  it.each([
    ['搜索资产跳转', '资产搜索上下文 asset-search 192.0.2.42'],
    ['搜索服务跳转', '服务搜索上下文 service-search'],
    ['搜索漏洞跳转', '漏洞搜索上下文 CVE-2026-1234 all'],
  ])('nav_search: %s forwards the exact selected target', async (action, expected) => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: action }));
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it('nav_search: direct navigation does not retain a previous search filter', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '搜索资产跳转' }));
    await screen.findByText('资产搜索上下文 asset-search 192.0.2.42');
    await userEvent.click(screen.getByRole('menuitem', { name: '服务器资产' }));
    expect(await screen.findByText('资产搜索上下文 全部')).toBeInTheDocument();
  });

  it('nav_search: selecting the same asset again restores its selected search context', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '搜索资产跳转' }));
    await userEvent.clear(await screen.findByRole('textbox', { name: '测试资产查询' }));
    await userEvent.type(screen.getByRole('textbox', { name: '测试资产查询' }), 'changed');
    await userEvent.click(screen.getByRole('button', { name: '搜索资产跳转' }));
    expect(screen.getByRole('textbox', { name: '测试资产查询' })).toHaveValue('192.0.2.42');
  });

  it('nav_search: session expiry clears selected objects before a different user signs in', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '搜索资产跳转' }));
    await screen.findByText('资产搜索上下文 asset-search 192.0.2.42');
    act(() => { window.dispatchEvent(new Event('sentinel:session-expired')); });
    await userEvent.click(await screen.findByRole('button', { name: '测试登录' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '服务器资产' }));
    expect(await screen.findByText('资产搜索上下文 全部')).toBeInTheDocument();
  });

  it('nav_workspace: opens the standalone operations assistant without replacing vulnerability AI', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    const menu = within(await screen.findByRole('menu', { name: '主导航' }));
    await userEvent.click(menu.getByText('AI 运维助手'));
    expect(await screen.findByText('AI 运维助手测试页')).toBeInTheDocument();
  });

  it('nav_alert: keeps the alert page open until the inline fallback is selected', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await screen.findByText('总览测试页');
    await userEvent.click(screen.getByRole('button', { name: '切换工作台' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '监控告警' }));
    await userEvent.click(screen.getByRole('menuitem', { name: '活动告警' }));
    await userEvent.click(await screen.findByRole('button', { name: '告警资产 SSH 跳转' }));
    expect(screen.queryByText('终端测试页 alert-asset-88')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '告警资产 SSH 跳转' })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: '在当前页打开 SSH' }));
    expect(await screen.findByText('终端测试页 alert-asset-88')).toBeInTheDocument();
  }, 15_000);

  it.each([
    ['打开测试资产终端', '终端测试页 asset-99'],
    ['查看测试资产漏洞', '漏洞测试页 asset-99'],
  ])('nav_dashboard: %s carries the asset ID to the destination', async (action, expected) => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: action }));
    if (action === '打开测试资产终端') {
      expect(screen.getByText('总览测试页')).toBeInTheDocument();
      expect(window.open).not.toHaveBeenCalled();
      await userEvent.click(await screen.findByRole('button', { name: '在当前页打开 SSH' }));
    }
    expect(await screen.findByText(expected)).toBeInTheDocument();
  }, 15_000);

  it('nav_assets: uses the same safe new-tab behavior from server assets', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    const menu = within(await screen.findByRole('menu', { name: '主导航' }));
    await userEvent.click(menu.getByRole('menuitem', { name: '服务器资产' }));
    await userEvent.click(await screen.findByRole('button', { name: '资产 SSH 跳转' }));
    expect(screen.getByRole('button', { name: '资产 SSH 跳转' })).toBeInTheDocument();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(window.open).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole('button', { name: '在当前页打开 SSH' }));
    expect(await screen.findByText('终端测试页 asset-99')).toBeInTheDocument();
  }, 15_000);

  it('nav_ssh_error: falls back inline when the browser rejects link navigation', async () => {
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(() => {
      throw new Error('Navigation unavailable');
    });
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '打开测试资产终端' }));
    expect(await screen.findByText('终端测试页 asset-99')).toBeInTheDocument();
    expect(screen.queryByText('已请求打开 SSH')).not.toBeInTheDocument();
  }, 15_000);

  it('nav_ssh_latest: replaces the fallback with the latest requested asset', async () => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '打开测试资产终端' }));
    await screen.findByRole('button', { name: '在当前页打开 SSH' });
    await userEvent.click(screen.getByRole('button', { name: '打开另一个资产终端' }));
    expect(screen.getAllByRole('button', { name: '在当前页打开 SSH' })).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: '在当前页打开 SSH' }));
    expect(await screen.findByText('终端测试页 asset-100')).toBeInTheDocument();
    expect(screen.queryByText('终端测试页 asset-99')).not.toBeInTheDocument();
  }, 15_000);

  it.each(['logout', 'session-expired'])('nav_ssh_auth: %s removes the previous user fallback action', async (action) => {
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await userEvent.click(await screen.findByRole('button', { name: '打开测试资产终端' }));
    await screen.findByRole('button', { name: '在当前页打开 SSH' });
    if (action === 'logout') await userEvent.click(screen.getByRole('button', { name: '退出登录' }));
    else act(() => { window.dispatchEvent(new Event('sentinel:session-expired')); });
    await userEvent.click(await screen.findByRole('button', { name: '测试登录' }));
    expect(await screen.findByText('总览测试页')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '在当前页打开 SSH' })).not.toBeInTheDocument();
    expect(screen.queryByText('终端测试页 asset-99')).not.toBeInTheDocument();
  }, 15_000);

  it.each(['logout', 'session-expired'])('nav_auth: %s clears the previous user navigation context', async (action) => {
    localStorage.setItem('sentinel_navigation:nav-user-b', JSON.stringify({ activePage: 'reports', security: 'reports' }));
    render(<ConfigProvider theme={{ token: { motion: false } }}><App /></ConfigProvider>);
    await screen.findByText('总览测试页');
    const menu = within(screen.getByRole('menu', { name: '主导航' }));
    await userEvent.click(menu.getByText('SSH 远程'));
    expect(screen.getByText('终端测试页')).toBeInTheDocument();
    if (action === 'logout') await userEvent.click(screen.getByRole('button', { name: '退出登录' }));
    else act(() => { window.dispatchEvent(new Event('sentinel:session-expired')); });
    await userEvent.click(await screen.findByRole('button', { name: '测试登录' }));
    expect(await screen.findByText('报告测试页')).toBeInTheDocument();
    expect(screen.queryByText('终端测试页')).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('sentinel_navigation:nav-user-a')!)).toMatchObject({ activePage: 'terminal' });
    expect(JSON.parse(localStorage.getItem('sentinel_navigation:nav-user-b')!)).toMatchObject({ activePage: 'reports' });
  }, 15_000);
});
