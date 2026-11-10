import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, ConfigProvider, Drawer, Dropdown, Layout, Menu, Tooltip } from 'antd';
import {
  Bot,
  Bell,
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
  LogOut,
} from 'lucide-react';
import type { PageKey } from '../App';
import type { AuthUser } from '../api/auth';
import { getNavigationPageKey } from '../types/security-check';
import { GlobalSearch } from './GlobalSearch';
import type { SearchTarget } from './GlobalSearch';
import { requestedPlatformPage, userDocsHref } from '../utils/userDocsNavigation';

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
    { key: 'assets', icon: <Server size={16} />, label: '服务器资产' },
    { key: 'cmdb', icon: <Network size={16} />, label: '系统/接口监控' },
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
    { key: 'system', icon: <Settings size={16} />, label: '系统状态' },
    { key: 'audit-log', icon: <FileText size={16} />, label: '审计日志' },
  ] },
];

const pageWorkspaces: Record<PageKey, WorkspaceKey> = {
  dashboard: 'workspace', assets: 'workspace', cmdb: 'workspace', automation: 'workspace', terminal: 'workspace', 'ops-assistant': 'workspace', patches: 'workspace',
  observability: 'monitoring', probes: 'monitoring', alerts: 'monitoring', incidents: 'monitoring',
  scans: 'security', schedules: 'security', baselines: 'security', exposures: 'security', 'credential-risks': 'security', findings: 'security', knowledge: 'security', agent: 'security', reports: 'security',
  system: 'system', credentials: 'system', 'audit-log': 'system',
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

function Navigation({ selectedPage, onNavigate, onSwitchWorkspace, collapsed = false }: {
  selectedPage: PageKey; onNavigate: (page: PageKey) => void;
  onSwitchWorkspace: (workspace: WorkspaceDefinition) => void;
  collapsed?: boolean;
}) {
  const activeWorkspace = workspaces.find((workspace) => workspace.key === pageWorkspaces[selectedPage]) ?? workspaces[0];
  const items = activeWorkspace.children.map((entry) => ({ key: entry.key, icon: entry.icon, label: entry.label }));
  return (
    <div className="nav-content">
      <div className="brand" aria-label="Linux 智能综合运维平台">
        <span className="brand-mark"><ShieldCheck size={22} /></span>
        <span><strong>Linux 智能综合运维平台</strong><small>Operations Platform</small></span>
      </div>
      <div className="workspace-nav-heading">
        <strong>{activeWorkspace.label}</strong>
        <Dropdown
          trigger={['click']}
          overlayClassName="workspace-switcher-dropdown"
          placement="bottomLeft"
          menu={{
            selectable: true,
            selectedKeys: [activeWorkspace.key],
            items: workspaces.map((workspace) => ({
              key: workspace.key, icon: workspace.icon, label: workspace.label,
              onClick: () => onSwitchWorkspace(workspace),
            })),
          }}
        >
          <Button type="text" className="workspace-switcher" aria-label="切换工作台"
            title="切换工作台" icon={<ArrowLeftRight size={18} />} />
        </Dropdown>
      </div>
      <ConfigProvider theme={{ token: { colorPrimary: '#1677ff' } }}>
      <Menu
        aria-label="主导航"
        className="nav-menu"
        mode="inline"
        theme="light"
        selectedKeys={[getNavigationPageKey(selectedPage)]}
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

const roleLabels = { admin: '管理员', operator: '操作员', auditor: '审计员' };
const pageLabels: Record<PageKey, string> = {
  alerts: '活动告警', dashboard: '综合总览', cmdb: '系统/接口监控', terminal: 'SSH 远程', observability: '服务监控',
  assets: '服务器资产', scans: '漏洞扫描', schedules: '安全检查', baselines: '安全检查',
  exposures: '安全检查', 'credential-risks': '安全检查', findings: '漏洞治理',
  reports: '安全报告', 'audit-log': '审计日志', patches: '补丁管理', credentials: '连接凭据',
  incidents: '故障管理',
  knowledge: '漏洞知识库', agent: '漏洞 AI 助手', system: '系统状态', automation: '自动化任务', 'ops-assistant': 'AI 运维助手',
  probes: 'API 拨测',
};

export function AppShell({ children, currentUser, onLogout, selectedPage, onNavigate, onSearchNavigate }: {
  children: ReactNode; currentUser: AuthUser; onLogout: () => void;
  selectedPage: PageKey; onNavigate: (page: PageKey) => void;
  onSearchNavigate?: (target: SearchTarget) => void;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
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
    if (state.restoring && state.restoring !== selectedPage) {
      onNavigate(state.restoring);
      return;
    }
    state.restoring = undefined;
    state.memory.activePage = selectedPage;
    state.memory[pageWorkspaces[selectedPage]] = selectedPage;
    writeNavigationMemory(currentUser.id, state.memory);
  }, [currentUser.id, onNavigate, selectedPage]);
  const switchWorkspace = (workspace: WorkspaceDefinition) => {
    if (workspace.key === activeWorkspace.key) return;
    const memory = preference.current?.userId === currentUser.id ? preference.current.memory : {};
    onNavigate(memory[workspace.key] ?? workspace.defaultPage);
    setMobileOpen(false);
  };
  return (
    <Layout className={`app-layout${collapsed ? ' sidebar-collapsed' : ''}`}>
      <Sider width={232} collapsedWidth={72} collapsed={collapsed} trigger={null} className="desktop-sider"><Navigation collapsed={collapsed} selectedPage={selectedPage} onNavigate={onNavigate} onSwitchWorkspace={switchWorkspace} /><Tooltip title={collapsed ? '展开侧栏' : '收起侧栏'}><Button className="sidebar-collapse" type="text" aria-label={collapsed ? '展开侧栏' : '收起侧栏'} icon={collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />} onClick={() => setCollapsed((value) => !value)} /></Tooltip></Sider>
      <Drawer
        className="mobile-navigation"
        placement="left"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        width={300}
        closable={false}
      >
        <Navigation selectedPage={selectedPage} onNavigate={(page) => { onNavigate(page); setMobileOpen(false); }} onSwitchWorkspace={switchWorkspace} />
      </Drawer>
      <Layout>
        <Header className="topbar">
          <Tooltip title="打开导航">
            <Button
              className="mobile-menu-button"
              type="text"
              icon={<MenuIcon size={20} />}
              aria-label="打开导航"
              onClick={() => setMobileOpen(true)}
            />
          </Tooltip>
          <div className="topbar-context"><span>{activeWorkspace.label}</span><strong>{pageLabels[selectedPage]}</strong></div>
          <GlobalSearch key={currentUser.id} onNavigate={onNavigate} onSearchNavigate={onSearchNavigate} />
          <div className="current-user">
            <span className="operator-avatar" aria-hidden="true">{currentUser.username.slice(0, 2).toUpperCase()}</span>
            <div className="user-meta"><strong>{currentUser.display_name || currentUser.username}</strong><span>{roleLabels[currentUser.role]}</span></div>
            <Tooltip title="退出登录">
              <Button type="text" aria-label="退出登录" icon={<LogOut size={17} />} onClick={onLogout} />
            </Tooltip>
          </div>
        </Header>
        <Content className="workspace">{children}</Content>
      </Layout>
    </Layout>
  );
}
