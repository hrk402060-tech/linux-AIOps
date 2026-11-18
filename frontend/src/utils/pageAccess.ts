import type { PageKey } from '../App';
import type { AuthUser } from '../api/auth';

type AccessSubject = Pick<AuthUser, 'role' | 'permissions'>;

export function isPolicyManaged(user: AccessSubject): boolean {
  return user.role !== 'admin' && user.permissions != null;
}

export function permissionScope(user: AuthUser | null): string {
  return user?.permissions == null ? 'legacy' : JSON.stringify([...new Set(user.permissions)].sort());
}

export function canAccessPage(user: AccessSubject, page: PageKey, action: 'read' | 'write' = 'read'): boolean {
  if (user.role === 'admin' || page === 'user-security') return true;
  if (action === 'write' && user.role === 'auditor') return false;
  if (page === 'settings' || page === 'security-settings' || page === 'user-authorization') return false;
  if (page === 'kubernetes-clusters') return false;
  if (!isPolicyManaged(user)) return true;
  return user.permissions?.includes(`${page}:${action}`) === true
    || (action === 'read' && user.role !== 'auditor' && user.permissions?.includes(`${page}:write`) === true);
}

const fallbacks: PageKey[] = [
  'dashboard', 'server-monitoring', 'assets', 'cmdb', 'automation', 'terminal', 'ops-assistant', 'patches',
  'observability', 'probes', 'alerts', 'incidents', 'scans', 'schedules', 'baselines', 'exposures',
  'credential-risks', 'findings', 'knowledge', 'agent', 'reports', 'system', 'audit-log', 'credentials', 'user-security',
];

export function firstAccessiblePage(user: AuthUser): PageKey {
  return fallbacks.find((page) => canAccessPage(user, page)) ?? 'user-security';
}
