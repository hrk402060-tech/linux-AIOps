import { workspaceDocs } from './workspace';
import { monitoringDocs } from './monitoring';
import { securityDocs } from './security';
import { systemDocs } from './system';
import type { UserDocEntry, UserDocGroup } from './types';

export const docGroups: { key: UserDocGroup; label: string }[] = [
  { key: 'workspace', label: '工作台' },
  { key: 'monitoring', label: '监控告警' },
  { key: 'security', label: '安全中心' },
  { key: 'system', label: '系统管理' },
];
export const userDocs: UserDocEntry[] = [
  ...workspaceDocs, ...monitoringDocs, ...securityDocs, ...systemDocs,
];

const diagnosticKeywords = 'SSH Docker K8s Kubernetes MySQL Redis RabbitMQ 授权 ' +
  'StrictHostKeyChecking known_hosts RBAC kubectl linuxops-readonly-v1 ' +
  '--askpass SHOW GLOBAL STATUS SHOW GLOBAL VARIABLES SHOW REPLICA STATUS SHOW SLAVE STATUS ' +
  'SLOWLOG GET 16 INFO server INFO memory INFO clients /api/overview /api/nodes';

export function matchesDoc(entry: UserDocEntry, query: string): boolean {
  const content = [entry.title, entry.purpose, ...entry.requirements, ...entry.steps,
    ...entry.notes, ...(entry.commands?.flatMap((command) => [command.title, command.text]) ?? []),
    entry.id === 'diagnostics' ? diagnosticKeywords : ''].join(' ').toLocaleLowerCase();
  return content.includes(query.trim().toLocaleLowerCase());
}
