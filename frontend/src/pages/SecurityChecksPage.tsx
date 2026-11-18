import { Tabs } from 'antd';
import './SecurityChecksPage.css';
import { ScanSchedulesPage } from './ScanSchedulesPage';
import { BaselinesPage } from './BaselinesPage';
import { ExposuresPage } from './ExposuresPage';
import { CredentialRisksPage } from './CredentialRisksPage';
import { isSecurityCheckPage, securityCheckTabs, type SecurityCheckPageKey } from '../types/security-check';
import type { AuthUser } from '../api/auth';
import { canAccessPage } from '../utils/pageAccess';

const panels = {
  schedules: ScanSchedulesPage,
  baselines: BaselinesPage,
  exposures: ExposuresPage,
  'credential-risks': CredentialRisksPage,
};

export function SecurityChecksPage({ activeKey, onNavigate, currentUser }: {
  activeKey: SecurityCheckPageKey; onNavigate: (page: SecurityCheckPageKey) => void;
  currentUser?: AuthUser;
}) {
  const available = securityCheckTabs.filter(({ key }) => !currentUser || canAccessPage(currentUser, key));
  return <section className="scan-tasks-page security-checks-page">
    <div className="page-heading"><h1>安全检查</h1></div>
    <Tabs className="events-tabs" activeKey={activeKey} destroyOnHidden
      onChange={(key) => { if (isSecurityCheckPage(key)) onNavigate(key); }}
      items={available.map(({ key, label }) => {
        const Panel = panels[key];
        return { key, label, children: <Panel embedded /> };
      })} />
  </section>;
}
