import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, ConfigProvider, Drawer, Dropdown, Layout, Menu, Tooltip } from 'antd';
import {
  Activity,
  Bot,
  Bell,
  Boxes,
  Database,
  FileWarning,
  FileText,
  ClipboardCheck,
  Gauge,
  Menu as MenuIcon,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  ArrowLeftRight,
  CircleHelp,
  ScanSearch,
  Server,
  Network,
  ShieldCheck,
  SquareTerminal,
  Wrench,
  Settings,
  Settings2,
  KeyRound,
  LogOut,
  Users,
} from 'lucide-react';
import type { PageKey } from '../App';
import type { AuthUser } from '../api/auth';
import { getNavigationPageKey, securityCheckTabs } from '../types/security-check';
import { canAccessPage, isPolicyManaged } from '../utils/pageAccess';
import { GlobalSearch } from './GlobalSearch';
import type { SearchTarget } from './GlobalSearch';
import { requestedPlatformPage, userDocsHref } from '../utils/userDocsNavigation';
import { usePlatformPreferences } from '../hooks/usePlatformPreferences';

const { Header, Sider, Content } = Layout;

type WorkspaceKey = 'workspace' | 'monitoring' | 'security' | 'system';
type NavigationItem = { key: PageKey; icon?: ReactNode; label: string };
type WorkspaceDefinition = {
  key: WorkspaceKey;
  label: string;
  icon: ReactNode;
  description: string;
  defaultPage: PageKey;
  children: NavigationItem[];
};

const workspaces: WorkspaceDefinition[] = [
  { key: 'workspace', label: '工作台', icon: <Gauge size={18} />, description: '资源与运维入口', defaultPage: 'dashboard', children: [
    { key: 'dashboard', icon: <Gauge size={16} />, label: '综合总览' },
    { key: 'server-monitoring', icon: <Activity size={16} />, label: '服务器监控' },
    { key: 'assets', icon: <Server size={16} />, label: '服务器资产' },
    { key: 'cmdb', icon: <Network size={16} />, label: '业务服务' },
    { key: 'kubernetes-clusters', icon: <Boxes size={16} />, label: 'Kubernetes 管理' },
    { key: 'automation', icon: <Wrench size={16} />, label: '自动化任务' },
    { key: 'terminal', icon: <SquareTerminal size={16} />, label: 'SSH 远程' },
    { key: 'ops-assistant', icon: <Bot size={16} />, label: 'AI 运维助手' },
    { key: 'patches', icon: <FileText size={16} />, label: '补丁管理' },
  ] },
  { key: 'monitoring', label: '监控告警', icon: <Radar size={18} />, description: '运行状态与事件', defaultPage: 'observability', children: [
    { key: 'observability', icon: <Gauge size={16} />, label: '服务监控' },
    { key: 'probes', icon: <Radar size={16} />, label: 'API 拨测' },
    { key: 'alerts', icon: <Bell size={16} />, label: '活动告警' },
    { key: 'incidents', icon: <ClipboardCheck size={16} />, label: '故障管理' },
  ] },
  { key: 'security', label: '安全中心', icon: <ShieldCheck size={18} />, description: '检测、治理与报告', defaultPage: 'scans', children: [
    { key: 'scans', icon: <ScanSearch size={16} />, label: '漏洞扫描' },
    { key: 'schedules', icon: <ClipboardCheck size={16} />, label: '安全检查' },
    { key: 'findings', icon: <FileWarning size={16} />, label: '漏洞治理' },
    { key: 'knowledge', icon: <Database size={16} />, label: '漏洞知识库' },
    { key: 'agent', icon: <Bot size={16} />, label: '漏洞 AI 助手' },
    { key: 'reports', icon: <FileText size={16} />, label: '安全报告' },
  ] },
  { key: 'system', label: '系统管理', icon: <Settings size={18} />, description: '用户、凭据与平台设置', defaultPage: 'system', children: [
    { key: 'settings', icon: <Settings2 size={16} />, label: '系统设置' },
    { key: 'security-settings', icon: <KeyRound size={16} />, label: '安全设置' },
    { key: 'system', icon: <Settings size={16} />, label: '平台状态' },
    { key: 'user-authorization', icon: <Users size={16} />, label: '用户与授权' },
    { key: 'audit-log', icon: <FileText size={16} />, label: '审计日志' },
  ] },
];

function accessibleWorkspaces(user: AuthUser): WorkspaceDefinition[] {
  const firstCheck = securityCheckTabs.find(({ key }) => canAccessPage(user, key))?.key;
  return workspaces.map((workspace) => {
    const children = workspace.children.flatMap((entry) => {
      const key = entry.key === 'schedules' ? firstCheck : entry.key;
      return key && canAccessPage(user, key) ? [{ ...entry, key }] : [];
    });
    const defaultPage = children.some(({ key }) => key === workspace.defaultPage)
      ? workspace.defaultPage : children[0]?.key ?? workspace.defaultPage;
    return { ...workspace, children, defaultPage };
  });
}

const pageWorkspaces: Record<PageKey, WorkspaceKey> = {
  dashboard: 'workspace', 'server-monitoring': 'workspace', assets: 'workspace', cmdb: 'workspace', automation: 'workspace', terminal: 'workspace', 'ops-assistant': 'workspace', patches: 'workspace',
  'kubernetes-clusters': 'workspace',
  observability: 'monitoring', probes: 'monitoring', alerts: 'monitoring', incidents: 'monitoring',
  scans: 'security', schedules: 'security', baselines: 'security', exposures: 'security', 'credential-risks': 'security', findings: 'security', knowledge: 'security', agent: 'security', reports: 'security',
  system: 'system', credentials: 'system', 'audit-log': 'system',
  settings: 'system', 'user-security': 'system', 'security-settings': 'system', 'user-authorization': 'system',
};

type NavigationMemory = Partial<Record<WorkspaceKey, PageKey>> & { activePage?: PageKey };
const navigationStorageKey = (userId: string) => `sentinel_navigation:${userId}`;
function readNavigationMemory(userId: string): NavigationMemory {
  try {
    const raw = localStorage.getItem(navigationStorageKey(userId));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const record = parsed as Record<string, unknown>;
    const memory: NavigationMemory = {};
    for (const workspace of workspaces) {
      const page = record[workspace.key];
      if (typeof page === 'string' && Object.hasOwn(pageWorkspaces, page) && pageWorkspaces[page as PageKey] === workspace.key) {
        memory[workspace.key] = page as PageKey;
      }
    }
    if (typeof record.activePage === 'string' && Object.hasOwn(pageWorkspaces, record.activePage)) {
      memory.activePage = record.activePage as PageKey;
    }
    return memory;
  } catch {
    console.warn('导航偏好读取失败，已使用默认工作台');
    return {};
  }
}
function writeNavigationMemory(userId: string, memory: NavigationMemory) {
  try {
    localStorage.setItem(navigationStorageKey(userId), JSON.stringify(memory));
  } catch {
    console.warn('导航偏好保存失败，不影响页面使用');
  }
}

function Navigation({ selectedPage, onNavigate, onSwitchWorkspace, collapsed = false, platformName, available }: {
  selectedPage: PageKey; onNavigate: (page: PageKey) => void;
  onSwitchWorkspace: (workspace: WorkspaceDefinition) => void;
  collapsed?: boolean;
  platformName: string;
  available: WorkspaceDefinition[];
}) {
  const activeWorkspace = available.find((workspace) => workspace.key === pageWorkspaces[selectedPage]) ?? available[0];
  const selectable = available.filter((workspace) => workspace.key !== 'system' && workspace.children.length > 0);
  const items = (activeWorkspace.key === 'system' ? [] : activeWorkspace.children)
    .map((entry) => ({ key: entry.key, icon: entry.icon, label: entry.label }));
  const selectedKey = getNavigationPageKey(selectedPage) === 'schedules'
    ? activeWorkspace.children.find((entry) => entry.label === '安全检查')?.key ?? selectedPage : selectedPage;
  return (
    <div className="nav-content">
      <div className="brand" aria-label={platformName}>
        <span className="brand-mark"><ShieldCheck size={22} /></span>
        <span><strong>{platformName}</strong><small>Operations Platform</small></span>
      </div>
      <div className="workspace-nav-heading">
        <strong>{activeWorkspace.label}</strong>
        {selectable.length > 0 ? <Dropdown
          trigger={['click']}
          overlayClassName="workspace-switcher-dropdown"
          placement="bottomLeft"
          menu={{
            selectable: true,
            selectedKeys: [activeWorkspace.key],
            items: selectable.map((workspace) => ({
              key: workspace.key, icon: workspace.icon, label: workspace.label,
              onClick: () => onSwitchWorkspace(workspace),
            })),
          }}
        >
          <Button type="text" className="workspace-switcher" aria-label="切换工作台"
            title="切换工作台" icon={<ArrowLeftRight size={18} />} />
        </Dropdown> : null}
      </div>
      <ConfigProvider theme={{ token: { colorPrimary: '#1677ff' } }}>
      <Menu
        aria-label="主导航"
        className="nav-menu"
        mode="inline"
        theme="light"
        selectedKeys={[selectedKey]}
        inlineCollapsed={collapsed}
        items={items}
        onClick={({ key }) => { if (Object.hasOwn(pageWorkspaces, key)) onNavigate(key as PageKey); }}
      />
      </ConfigProvider>
      <div className="nav-footer">
        <div className="nav-status"><Radar size={16} /><span><strong>平台运行正常</strong><small>服务连接稳定</small></span></div>
        <a className="nav-help" aria-label="打开使用文档" href={userDocsHref()}
          target="_blank" rel="noopener noreferrer">
          <CircleHelp size={16} />
          <span><strong>使用文档</strong><small>功能说明 · 准备条件 · 操作命令</small></span>
        </a>
      </div>
    </div>
  );
}

const pageLabels: Record<PageKey, string> = {
  alerts: '活动告警', dashboard: '综合总览', 'server-monitoring': '服务器监控', cmdb: '业务服务', terminal: 'SSH 远程', observability: '服务监控',
  assets: '服务器资产', scans: '漏洞扫描', schedules: '安全检查', baselines: '安全检查',
  exposures: '安全检查', 'credential-risks': '安全检查', findings: '漏洞治理',
  reports: '安全报告', 'audit-log': '审计日志', patches: '补丁管理', credentials: '连接凭据',
  incidents: '故障管理',
  knowledge: '漏洞知识库', agent: '漏洞 AI 助手', system: '平台状态', automation: '自动化任务', 'ops-assistant': 'AI 运维助手',
  settings: '系统设置', 'user-security': '个人安全', 'security-settings': '系统设置', 'user-authorization': '用户与授权',
  probes: 'API 拨测',
  'kubernetes-clusters': 'Kubernetes 管理',
};

export function AppShell({ children, currentUser, onLogout, selectedPage, onNavigate, onSearchNavigate, hideNavigation = false }: {
  children: ReactNode; currentUser: AuthUser; onLogout: () => void;
  selectedPage: PageKey; onNavigate: (page: PageKey) => void;
  onSearchNavigate?: (target: SearchTarget) => void;
  hideNavigation?: boolean;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const platform = usePlatformPreferences(currentUser.id, selectedPage, onNavigate);
  const available = accessibleWorkspaces(currentUser);
  const preference = useRef<{ userId: string; memory: NavigationMemory; restoring?: PageKey } | null>(null);
  const activeWorkspace = workspaces.find((workspace) => workspace.key === pageWorkspaces[selectedPage]) ?? workspaces[0];
  useEffect(() => {
    if (preference.current?.userId !== currentUser.id) {
      const changingUser = preference.current !== null;
      const memory = readNavigationMemory(currentUser.id);
      preference.current = { userId: currentUser.id, memory,
        restoring: changingUser ? memory.activePage ?? 'dashboard'
          : selectedPage === 'dashboard' && !requestedPlatformPage() ? memory.activePage : undefined };
    }
    const state = preference.current;
    if (state.restoring && state.restoring !== selectedPage && canAccessPage(currentUser, state.restoring)) {
      onNavigate(state.restoring);
      return;
    }
    state.restoring = undefined;
    state.memory.activePage = selectedPage;
    state.memory[pageWorkspaces[selectedPage]] = selectedPage;
    writeNavigationMemory(currentUser.id, state.memory);
  }, [currentUser, onNavigate, selectedPage]);
  const switchWorkspace = (workspace: WorkspaceDefinition) => {
    if (workspace.key === activeWorkspace.key) return;
    const memory = preference.current?.userId === currentUser.id ? preference.current.memory : {};
    const remembered = memory[workspace.key];
    onNavigate(remembered && canAccessPage(currentUser, remembered) ? remembered : workspace.defaultPage);
    setMobileOpen(false);
  };
  return (
    <ConfigProvider componentSize={platform.display_density === 'compact' ? 'small' : 'middle'}>
    <Layout data-density={platform.display_density} className={`app-layout${collapsed ? ' sidebar-collapsed' : ''}${hideNavigation ? ' app-layout-settings' : ''}`}>
      {!hideNavigation ? <Sider width={232} collapsedWidth={72} collapsed={collapsed} trigger={null} className="desktop-sider"><Navigation available={available} platformName={platform.platform_name} collapsed={collapsed} selectedPage={selectedPage} onNavigate={onNavigate} onSwitchWorkspace={switchWorkspace} /><Tooltip title={collapsed ? '展开侧栏' : '收起侧栏'}><Button className="sidebar-collapse" type="text" aria-label={collapsed ? '展开侧栏' : '收起侧栏'} icon={collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />} onClick={() => setCollapsed((value) => !value)} /></Tooltip></Sider> : null}
      {!hideNavigation ? <Drawer
        className="mobile-navigation"
        placement="left"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        width={300}
        closable={false}
      >
        <Navigation available={available} platformName={platform.platform_name} selectedPage={selectedPage} onNavigate={(page) => { onNavigate(page); setMobileOpen(false); }} onSwitchWorkspace={switchWorkspace} />
      </Drawer> : null}
      <Layout>
        <Header className="topbar">
          {!hideNavigation ? <Tooltip title="打开导航">
            <Button
              className="mobile-menu-button"
              type="text"
              icon={<MenuIcon size={20} />}
              aria-label="打开导航"
              onClick={() => setMobileOpen(true)}
            />
          </Tooltip> : null}
          <div className="topbar-context"><span>{activeWorkspace.label}</span><strong>{pageLabels[selectedPage]}</strong></div>
          {!isPolicyManaged(currentUser) ? <GlobalSearch key={currentUser.id} onNavigate={onNavigate} onSearchNavigate={onSearchNavigate} /> : null}
          <div className="topbar-account-actions">
          {currentUser.role === 'admin' ? <Tooltip title="系统设置"><Button type="text" className="settings-gear" aria-label="打开系统设置" icon={<Settings size={18} />} onClick={() => onNavigate('settings')} /></Tooltip> : null}
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            menu={{
              items: [
                { key: 'user-security', icon: <KeyRound size={16} />, label: '个人安全',
                  onClick: () => onNavigate('user-security') },
                ...(currentUser.role === 'admin' ? [{ key: 'user-authorization', icon: <Users size={16} />, label: '用户与授权',
                  onClick: () => onNavigate('user-authorization') }] : []),
                ...(canAccessPage(currentUser, 'system') ? [{ key: 'system', icon: <Settings size={16} />, label: '平台状态',
                  onClick: () => onNavigate('system') }] : []),
                ...(canAccessPage(currentUser, 'audit-log') ? [{ key: 'audit-log', icon: <FileText size={16} />, label: '审计日志',
                  onClick: () => onNavigate('audit-log') }] : []),
                { type: 'divider' },
                { key: 'logout', danger: true, icon: <LogOut size={16} />, label: '退出登录',
                  onClick: onLogout },
              ],
            }}
          >
            <button type="button" className="current-user" aria-label="用户菜单">
              <span className="operator-avatar" aria-hidden="true">{currentUser.username.slice(0, 2).toUpperCase()}</span>
            </button>
          </Dropdown>
          </div>
        </Header>
        <Content className="workspace">{children}</Content>
      </Layout>
    </Layout>
    </ConfigProvider>
  );
}
