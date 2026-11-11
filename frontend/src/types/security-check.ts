import type { PageKey } from '../App';

export const securityCheckTabs = [
  { key: 'schedules', label: '定时扫描' },
  { key: 'baselines', label: '基线合规' },
  { key: 'exposures', label: '暴露面检测' },
  { key: 'credential-risks', label: '弱口令评估' },
] as const;

export type SecurityCheckPageKey = typeof securityCheckTabs[number]['key'];

export function isSecurityCheckPage(key: string): key is SecurityCheckPageKey {
  return securityCheckTabs.some((tab) => tab.key === key);
}

export function getNavigationPageKey(page: PageKey): PageKey {
  if (page === 'user-security') return 'security-settings';
  return isSecurityCheckPage(page) ? 'schedules' : page;
}
