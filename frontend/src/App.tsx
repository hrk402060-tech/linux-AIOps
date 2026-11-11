import { useCallback, useEffect, useState } from 'react';
import { Button, notification, Spin } from 'antd';
import { SquareTerminal } from 'lucide-react';

import { logout } from './api/auth';
import { useCurrentUser } from './hooks/useCurrentUser';
import { AccessGate } from './components/AccessGate';
import { AppShell } from './components/AppShell';
import type { SearchTarget } from './components/GlobalSearch';
import { PasswordChangeGate } from './components/PasswordChangeGate';
import { CredentialsPage } from './pages/CredentialsPage';
import { AssetsPage } from './pages/AssetsPage';
import { KnowledgePage } from './pages/KnowledgePage';
import { ScanTasksPage } from './pages/ScanTasksPage';
import { VulnerabilitiesPage } from './pages/VulnerabilitiesPage';
import { DashboardPage } from './pages/DashboardPage';
import { ServerMonitoringPreviewPage } from './pages/ServerMonitoringPreviewPage';
import { AIAgentPage } from './pages/AIAgentPage';
import { ReportsPage } from './pages/ReportsPage';
import { TerminalPage } from './pages/TerminalPage';
import { EventsPage } from './pages/EventsPage';
import { SecurityChecksPage } from './pages/SecurityChecksPage';
import { isSecurityCheckPage } from './types/security-check';
import { AuditLogPage } from './pages/AuditLogPage';
import { PatchesPage } from './pages/PatchesPage';
import { SystemPage } from './pages/SystemPage';
import { SecuritySettingsPage } from './pages/SecuritySettingsPage';
import { AutomationPage } from './pages/AutomationPage';
import { CmdbPage } from './pages/CmdbPage';
import { ObservabilityPage } from './pages/ObservabilityPage';
import { ProbesPage } from './pages/ProbesPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { OperationsAssistantPage } from './pages/OperationsAssistantPage';
import { UserDocsPage } from './pages/UserDocsPage';
import { openTerminalTab } from './utils/terminalNavigation';
import { requestedPlatformPage } from './utils/userDocsNavigation';

export type PageKey = 'dashboard' | 'server-monitoring' | 'cmdb' | 'terminal' | 'observability' | 'probes' | 'assets' | 'scans' | 'schedules' | 'baselines' | 'exposures' | 'credential-risks' | 'findings' | 'incidents' | 'reports' | 'audit-log' | 'patches' | 'credentials' | 'knowledge' | 'agent' | 'alerts' | 'system' | 'automation' | 'ops-assistant' | 'user-security' | 'security-settings';

export default function App() {
  const [monitorPreview, setMonitorPreview] = useState(() => import.meta.env.VITE_SERVER_MONITOR_PREVIEW === 'true'
    && new URLSearchParams(window.location.search).get('view') === 'server-monitoring-preview');
  const [page, setPage] = useState<PageKey>(() => requestedPlatformPage() ?? 'dashboard');
  const [findingAssetId, setFindingAssetId] = useState<string | null>(null);
  const [terminalAssetId, setTerminalAssetId] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<SearchTarget | null>(null);
  const [searchNavigationId, setSearchNavigationId] = useState(0);
  const [terminalNotice, terminalNoticeHolder] = notification.useNotification();
  const resetScopedState = useCallback(() => {
    setPage('dashboard');
    setFindingAssetId(null);
    setTerminalAssetId(null);
    setSearchTarget(null);
  }, []);
  const { currentUser, authLoading, setCurrentUser } = useCurrentUser(resetScopedState);
  const scopeKey = `${currentUser?.id ?? ''}:${currentUser?.role ?? ''}:${currentUser?.access_revision ?? 0}`;
  const standaloneTerminalAssetId = new URLSearchParams(window.location.search).get('terminal_asset');
  const navigate = (nextPage: PageKey) => {
    if (monitorPreview) {
      setMonitorPreview(false);
      const url = new URL(window.location.href);
      url.searchParams.delete('view');
      window.history.replaceState(null, '', url);
    }
    setSearchTarget(null);
    if (nextPage !== 'findings' || searchTarget?.page === 'findings') setFindingAssetId(null);
    if (nextPage !== 'terminal') setTerminalAssetId(null);
    setPage(nextPage === 'user-security' ? 'security-settings' : nextPage);
  };
  const navigateToSearchResult = (target: SearchTarget) => {
    navigate(target.page);
    setSearchTarget(target);
    setSearchNavigationId((value) => value + 1);
    if (target.page === 'findings') setFindingAssetId(target.assetId);
  };
  const viewAssetFindings = (assetId: string) => {
    setSearchTarget(null);
    setFindingAssetId(assetId);
    setPage('findings');
  };
  const openTerminalInPage = (assetId: string) => {
    setSearchTarget(null);
    terminalNotice.destroy('terminal-tab-request');
    setTerminalAssetId(assetId);
    setPage('terminal');
  };
  const openAssetTerminal = (assetId: string) => {
    try {
      openTerminalTab(assetId);
    } catch {
      openTerminalInPage(assetId);
      return;
    }
    terminalNotice.info({
      key: 'terminal-tab-request',
      message: '已请求打开 SSH',
      duration: 8,
      actions: <Button size="small" icon={<SquareTerminal size={15} />}
        onClick={() => openTerminalInPage(assetId)}>在当前页打开 SSH</Button>,
    });
  };
  useEffect(() => () => terminalNotice.destroy('terminal-tab-request'), [scopeKey, terminalNotice]);
  const signOut = async () => {
    try { await logout(); } finally { setCurrentUser(null); resetScopedState(); }
  };
  if (authLoading) return <main className="auth-loading"><Spin size="large" /></main>;
  if (!currentUser) return <AccessGate onAuthenticated={setCurrentUser} />;
  if (currentUser.must_change_password) {
    return <PasswordChangeGate onChanged={setCurrentUser} />;
  }
  if (new URLSearchParams(window.location.search).get('view') === 'docs') {
    return <UserDocsPage key={scopeKey} />;
  }
  if (monitorPreview) {
    return <AppShell key={scopeKey} currentUser={currentUser} onLogout={() => void signOut()} selectedPage="server-monitoring"
      onNavigate={navigate} onSearchNavigate={navigateToSearchResult}>
      {terminalNoticeHolder}
      <ServerMonitoringPreviewPage key={scopeKey} canManage={currentUser.role !== 'auditor'} />
    </AppShell>;
  }
  if (standaloneTerminalAssetId) {
    return <TerminalPage key={scopeKey} initialAssetId={standaloneTerminalAssetId} standalone />;
  }
  return (
    <AppShell key={scopeKey} currentUser={currentUser} onLogout={() => void signOut()} selectedPage={page}
      onNavigate={navigate} onSearchNavigate={navigateToSearchResult}>
      {terminalNoticeHolder}
      {page === 'dashboard' ? <DashboardPage onNavigate={navigate} onViewFindings={viewAssetFindings} onOpenTerminal={openAssetTerminal} />
        : page === 'server-monitoring' ? <ServerMonitoringPreviewPage key={scopeKey} canManage={currentUser.role !== 'auditor'} />
        : page === 'cmdb' ? <CmdbPage currentUser={currentUser}
          key={searchTarget?.page === 'cmdb' ? `${searchTarget.serviceId}:${searchNavigationId}` : 'cmdb'}
          initialServiceId={searchTarget?.page === 'cmdb' ? searchTarget.serviceId : undefined} />
        : page === 'system' ? <SystemPage currentUser={currentUser} onLogout={() => void signOut()} />
        : page === 'user-security' || page === 'security-settings'
          ? <SecuritySettingsPage currentUser={currentUser} onLogout={() => void signOut()} />
        : page === 'patches' ? <PatchesPage />
        : page === 'automation' ? <AutomationPage onNavigate={navigate} />
        : page === 'audit-log' ? <AuditLogPage />
        : isSecurityCheckPage(page) ? <SecurityChecksPage activeKey={page} onNavigate={navigate} />
        : page === 'alerts' ? <EventsPage currentUser={currentUser} onOpenTerminal={openAssetTerminal} onOpenIncidents={() => navigate('incidents')} />
        : page === 'incidents' ? <IncidentsPage currentUser={currentUser} />
        : page === 'observability' ? <ObservabilityPage />
        : page === 'probes' ? <ProbesPage currentUser={currentUser} />
        : page === 'terminal' ? <TerminalPage initialAssetId={terminalAssetId} />
        : page === 'ops-assistant' ? <OperationsAssistantPage userId={currentUser.id} />
            : page === 'assets' ? <AssetsPage onViewFindings={viewAssetFindings} onOpenTerminal={openAssetTerminal}
              key={searchTarget?.page === 'assets' ? `${searchTarget.assetId}:${searchNavigationId}` : 'assets'}
              initialAssetId={searchTarget?.page === 'assets' ? searchTarget.assetId : null}
              initialQuery={searchTarget?.page === 'assets' ? searchTarget.query : ''} /> : page === 'scans' ? <ScanTasksPage currentUser={currentUser} />
        : page === 'findings' ? <VulnerabilitiesPage initialAssetId={findingAssetId}
          key={`${findingAssetId ?? 'all'}:${searchTarget?.page === 'findings' ? `${searchTarget.query}:${searchNavigationId}` : ''}`}
          initialQuery={searchTarget?.page === 'findings' ? searchTarget.query : ''}
          initialDisposition={searchTarget?.page === 'findings' ? 'all' : 'active'} />
          : page === 'reports' ? <ReportsPage />
          : page === 'credentials' ? <CredentialsPage />
            : page === 'knowledge' ? <KnowledgePage /> : <AIAgentPage currentUser={currentUser} />}
    </AppShell>
  );
}
