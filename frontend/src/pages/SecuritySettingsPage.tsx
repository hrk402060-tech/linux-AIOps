import type { AuthUser } from '../api/auth';
import { AuthPolicyPanel } from '../components/AuthPolicyPanel';
import { AISettingsPanel } from '../components/AISettingsPanel';
import { UserSecurityPage } from './UserSecurityPage';

export function SecuritySettingsPage({ currentUser, onLogout }: { currentUser: AuthUser; onLogout: () => void }) {
  return <section className="system-page">
    <div className="page-heading">
      <div><p className="eyebrow">账号与平台安全</p><h1>安全设置</h1></div>
    </div>
    <UserSecurityPage key={currentUser.id} embedded onLogout={onLogout} />
    {currentUser.role === 'admin' ? <>
      <AuthPolicyPanel />
      <AISettingsPanel />
    </> : null}
  </section>;
}
