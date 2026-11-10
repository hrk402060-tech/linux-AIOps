import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode, useState } from 'react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';
import type { PageKey } from '../App';
import { getNavigationPageKey } from '../types/security-check';

const currentUser = {
  id: 'user-1', username: 'security-admin', display_name: '安全管理员',
  role: 'admin' as const, enabled: true, must_change_password: false,
  created_at: '2026-09-15T00:00:00Z', last_login_at: null,
};
const expectedPages: Record<PageKey, [string, string]> = {
  dashboard: ['工作台', '综合总览'], cmdb: ['工作台', '系统/接口监控'],
  assets: ['工作台', '服务器资产'], automation: ['工作台', '自动化任务'],
  terminal: ['工作台', 'SSH 远程'], 'ops-assistant': ['工作台', 'AI 运维助手'], patches: ['工作台', '补丁管理'],
  observability: ['监控告警', '服务监控'], probes: ['监控告警', 'API 拨测'],
  alerts: ['监控告警', '活动告警'], incidents: ['监控告警', '故障管理'],
  scans: ['安全中心', '漏洞扫描'], schedules: ['安全中心', '安全检查'],
  baselines: ['安全中心', '安全检查'], exposures: ['安全中心', '安全检查'],
  'credential-risks': ['安全中心', '安全检查'], findings: ['安全中心', '漏洞治理'],
  knowledge: ['安全中心', '漏洞知识库'], agent: ['安全中心', '漏洞 AI 助手'],
  reports: ['安全中心', '安全报告'], system: ['系统管理', '系统状态'],
  credentials: ['系统管理', '连接凭据'], 'audit-log': ['系统管理', '审计日志'],
};
const groupEntries: Record<string, string[]> = {
  工作台: ['综合总览', '服务器资产', '系统/接口监控', '自动化任务', 'SSH 远程', 'AI 运维助手', '补丁管理'],
  监控告警: ['服务监控', 'API 拨测', '活动告警', '故障管理'],
  安全中心: ['漏洞扫描', '安全检查', '漏洞治理', '漏洞知识库', '漏洞 AI 助手', '安全报告'],
  系统管理: ['系统状态', '审计日志'],
};

function Harness({ initialPage = 'dashboard', user = currentUser }: {
  initialPage?: PageKey; user?: typeof currentUser;
}) {
  const [page, setPage] = useState<PageKey>(initialPage);
  return <ConfigProvider theme={{ token: { motion: false } }}>
    <AppShell currentUser={user} onLogout={vi.fn()} selectedPage={page} onNavigate={setPage}>
      <output>{page}</output>
    </AppShell>
  </ConfigProvider>;
}
function menu() { return within(screen.getByRole('menu', { name: '主导航' })); }
async function switchTo(label: string) {
  fireEvent.click(screen.getByRole('button', { name: '切换工作台' }));
  let popup: HTMLElement | undefined;
  await waitFor(() => {
    popup = Array.from(document.querySelectorAll<HTMLElement>('.workspace-switcher-dropdown'))
      .find((node) => !node.classList.contains('ant-dropdown-hidden'));
    expect(popup).toBeDefined();
  });
  const item = within(popup as HTMLElement).getByRole('menuitem', { name: new RegExp(`^${label}$`) });
  fireEvent.click(item);
  await waitFor(() => expect((popup as HTMLElement).classList.contains('ant-dropdown-hidden')).toBe(true));
}

beforeEach(() => { localStorage.clear(); });

describe('AppShell 四工作台导航', () => {
  it.each(Object.keys(expectedPages) as PageKey[])('保留 %s 的菜单映射和原 PageKey', async (page) => {
    const onNavigate = vi.fn();
    const { container } = render(<AppShell currentUser={currentUser} onLogout={vi.fn()}
      selectedPage={page} onNavigate={onNavigate}>内容</AppShell>);
    const [group, label] = expectedPages[page];
    const entry = container.querySelector<HTMLElement>('.desktop-sider .ant-menu-item-selected');
    if (page === 'credentials') {
      expect(entry).toBeNull();
    } else {
      expect(entry).toHaveTextContent(label);
    }
    expect(container.querySelectorAll('.desktop-sider .ant-menu-submenu')).toHaveLength(0);
    expect(container.querySelector('.workspace-nav-heading strong')).toHaveTextContent(group);
    expect(container.querySelector('.topbar-context span')).toHaveTextContent(group);
    expect(container.querySelector('.topbar-context strong')).toHaveTextContent(label);
    expect(menu().getAllByRole('menuitem').map((item) => item.textContent)).toEqual(groupEntries[group]);
    if (entry) {
      await userEvent.click(entry);
      expect(onNavigate).toHaveBeenCalledWith(getNavigationPageKey(page));
    }
  });

  it('nav_interaction: switches workspaces, visible menus and header together', async () => {
    const { container } = render(<Harness />);
    for (const [group, page] of [['监控告警', 'observability'], ['安全中心', 'scans'], ['系统管理', 'system'], ['工作台', 'dashboard']]) {
      await switchTo(group);
      expect(screen.getByRole('status')).toHaveTextContent(page);
      expect(menu().getAllByRole('menuitem').map((item) => item.textContent)).toEqual(groupEntries[group]);
      expect(container.querySelector('.topbar-context span')).toHaveTextContent(group);
    }
    expect(screen.queryByText('工单管理')).not.toBeInTheDocument();
    expect(screen.queryByText('AI 运维')).not.toBeInTheDocument();
  }, 30_000);

  it('nav_popup: sidebar trigger opens only four workspaces and marks the current one', async () => {
    const { container } = render(<Harness />);
    expect(container.querySelector('.topbar .workspace-switcher')).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /^监控告警$/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换工作台' }));
    const item = await screen.findByRole('menuitem', { name: /^工作台$/ });
    const popup = item.closest('.workspace-switcher-dropdown');
    expect(popup).not.toBeNull();
    expect(within(popup as HTMLElement).getAllByRole('menuitem').map((entry) => entry.textContent))
      .toEqual(Object.keys(groupEntries));
    expect(item).toHaveClass('ant-dropdown-menu-item-selected');
    fireEvent.click(item);
    await waitFor(() => expect(item).not.toBeVisible());
    expect(screen.getByRole('status')).toHaveTextContent('dashboard');
  });

  it('切回工作台恢复最近页面，再次点击当前工作台不重置 SSH', async () => {
    render(<Harness initialPage="terminal" />);
    await switchTo('工作台');
    expect(screen.getByRole('status')).toHaveTextContent('terminal');
    await switchTo('监控告警');
    await userEvent.click(menu().getByText('活动告警'));
    await switchTo('工作台');
    expect(screen.getByRole('status')).toHaveTextContent('terminal');
    await switchTo('监控告警');
    expect(screen.getByRole('status')).toHaveTextContent('alerts');
  }, 30_000);

  it('外部跨页跳转自动切换工作台，保留安全检查子页', () => {
    const props = { currentUser, onLogout: vi.fn(), onNavigate: vi.fn() };
    const { container, rerender } = render(<AppShell {...props} selectedPage="dashboard">内容</AppShell>);
    for (const page of ['baselines', 'exposures', 'credential-risks', 'schedules'] as PageKey[]) {
      rerender(<AppShell {...props} selectedPage={page}>内容</AppShell>);
      expect(container.querySelector('.workspace-nav-heading strong')).toHaveTextContent('安全中心');
      expect(container.querySelector('.ant-menu-item-selected')).toHaveTextContent('安全检查');
      expect(props.onNavigate).not.toHaveBeenCalled();
    }
  });

  it('离开再返回安全中心保留安全检查具体页签', async () => {
    render(<Harness initialPage="exposures" />);
    await switchTo('监控告警');
    await switchTo('安全中心');
    expect(screen.getByRole('status')).toHaveTextContent('exposures');
  }, 15_000);

  it('刷新后恢复当前用户最近页面，不共享其他用户偏好', async () => {
    const first = render(<Harness initialPage="reports" />);
    first.unmount();
    const second = render(<Harness />);
    expect(await screen.findByRole('status')).toHaveTextContent('reports');
    second.unmount();
    render(<Harness user={{ ...currentUser, id: 'another-user' }} />);
    expect(screen.getByRole('status')).toHaveTextContent('dashboard');
  });

  it('nav_cache: StrictMode restores the saved page without overwriting it', () => {
    localStorage.setItem('sentinel_navigation:user-1', JSON.stringify({ activePage: 'reports', security: 'reports' }));
    render(<StrictMode><Harness /></StrictMode>);
    expect(screen.getByRole('status')).toHaveTextContent('reports');
    expect(JSON.parse(localStorage.getItem('sentinel_navigation:user-1')!)).toMatchObject({ activePage: 'reports', security: 'reports' });
  });

  it('nav_cache: changing authenticated users restores their own workspace', () => {
    localStorage.setItem('sentinel_navigation:user-2', JSON.stringify({ activePage: 'incidents', monitoring: 'incidents' }));
    const { rerender } = render(<Harness initialPage="reports" />);
    rerender(<Harness user={{ ...currentUser, id: 'user-2' }} />);
    expect(screen.getByRole('status')).toHaveTextContent('incidents');
    expect(JSON.parse(localStorage.getItem('sentinel_navigation:user-1')!)).toMatchObject({ activePage: 'reports' });
  });

  it('nav_cache: a cached page in the wrong workspace cannot replace its default', async () => {
    localStorage.setItem('sentinel_navigation:user-1', JSON.stringify({ monitoring: 'reports' }));
    render(<Harness />);
    await switchTo('监控告警');
    expect(screen.getByRole('status')).toHaveTextContent('observability');
  });

  it.each(['not-json', '{"activePage":"unknown"}', '{"activePage":"__proto__"}', '{"activePage":"constructor"}'])(
    '非法导航缓存安全回退：%s', (value) => {
      localStorage.setItem('sentinel_navigation:user-1', value);
      render(<Harness />);
      expect(screen.getByRole('status')).toHaveTextContent('dashboard');
    },
  );

  it('浏览器存储不可用不阻止导航', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage disabled'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage disabled'); });
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    try {
      render(<Harness />);
      await switchTo('监控告警');
      expect(screen.getByRole('status')).toHaveTextContent('observability');
      expect(warning).toHaveBeenCalled();
    } finally { vi.restoreAllMocks(); }
  });

  it.each(['admin', 'operator', 'auditor'] as const)('角色 %s 保留现有入口和系统页权限边界', async (role) => {
    const { container } = render(<AppShell currentUser={{ ...currentUser, role }} onLogout={vi.fn()}
      selectedPage="system" onNavigate={vi.fn()}>内容</AppShell>);
    expect(menu().getAllByRole('menuitem').map((item) => item.textContent)).toEqual(groupEntries.系统管理);
    expect(container.querySelector('.ant-menu-item-selected')).toHaveTextContent('系统状态');
    expect(menu().queryByText('用户权限')).not.toBeInTheDocument();
    expect(menu().queryByText('AI 配置')).not.toBeInTheDocument();
  });

  it('系统管理不再展示连接凭据入口，但保留旧 PageKey 兼容', () => {
    render(<AppShell currentUser={currentUser} onLogout={vi.fn()}
      selectedPage="system" onNavigate={vi.fn()}>内容</AppShell>);
    expect(menu().getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['系统状态', '审计日志']);
    expect(menu().queryByText('连接凭据')).not.toBeInTheDocument();
  });

  it('nav_interaction: collapsed sidebar keeps the workspace switcher available', async () => {
    const { container } = render(<Harness initialPage="terminal" />);
    await userEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeInTheDocument();
    await switchTo('安全中心');
    expect(screen.getByRole('status')).toHaveTextContent('scans');
    expect(container.querySelector('.ant-menu-item-selected')).toHaveTextContent('漏洞扫描');
    await userEvent.click(screen.getByRole('button', { name: '展开侧栏' }));
    expect(menu().getByText('安全检查')).toBeInTheDocument();
  }, 15_000);

  it('左下角使用文档通过安全链接在新标签打开，当前页面保持', () => {
    render(<Harness />);
    const link = screen.getByRole('link', { name: '打开使用文档' });
    expect(link).toHaveAttribute('href', `${window.location.origin}/?view=docs`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('status')).toHaveTextContent('dashboard');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('折叠侧栏仍保留使用文档的新标签入口', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
    expect(screen.getByRole('link', { name: '打开使用文档' })).toBeVisible();
    expect(screen.getByRole('link', { name: '打开使用文档' })).toHaveAttribute('target', '_blank');
  });

  it('导航抽屉中的使用文档使用同一独立页面', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    const drawer = within(await screen.findByRole('dialog'));
    expect(drawer.getByRole('link', { name: '打开使用文档' }))
      .toHaveAttribute('href', `${window.location.origin}/?view=docs`);
    expect(drawer.getByRole('link', { name: '打开使用文档' })).toHaveAttribute('target', '_blank');
  });

  it('抽屉只显示当前工作台菜单，点击页面后关闭', async () => {
    render(<Harness initialPage="alerts" />);
    await userEvent.click(screen.getByRole('button', { name: '打开导航' }));
    const drawer = within(await screen.findByRole('dialog'));
    expect(drawer.queryByText('漏洞扫描')).not.toBeInTheDocument();
    await userEvent.click(drawer.getByText('API 拨测'));
    expect(screen.getByRole('status')).toHaveTextContent('probes');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('保留亮色导航、品牌与退出登录', async () => {
    const onLogout = vi.fn();
    render(<AppShell currentUser={currentUser} onLogout={onLogout}
      selectedPage="dashboard" onNavigate={vi.fn()}>内容</AppShell>);
    expect(screen.getByRole('menu', { name: '主导航' })).toHaveClass('ant-menu-light');
    expect(screen.getByLabelText('Linux 智能综合运维平台')).toBeInTheDocument();
    expect(screen.getByText('安全管理员')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }));
    expect(onLogout).toHaveBeenCalledOnce();
  });
});
