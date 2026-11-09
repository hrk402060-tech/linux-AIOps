import type { AssetEnvironment, AssetStatus, OsFamily } from '../types/asset';

export const osOptions: { value: OsFamily; label: string }[] = [
  { value: 'auto', label: '自动识别' }, { value: 'openeuler', label: 'openEuler' },
  { value: 'kylin', label: '麒麟 Linux' }, { value: 'uos', label: '统信 UOS' },
  { value: 'anolis', label: '龙蜥 Anolis OS' }, { value: 'ubuntu', label: 'Ubuntu' },
  { value: 'debian', label: 'Debian' }, { value: 'rhel', label: 'Red Hat Enterprise Linux' },
  { value: 'centos', label: 'CentOS' }, { value: 'rocky', label: 'Rocky Linux' },
  { value: 'almalinux', label: 'AlmaLinux' }, { value: 'other', label: '其他 Linux' },
];

export const osLabels = Object.fromEntries(osOptions.map(({ value, label }) => [value, label])) as Record<OsFamily, string>;
export const environmentLabels: Record<AssetEnvironment, string> = { production: '生产', staging: '预发布', test: '测试', development: '开发', other: '其他' };
export const statusLabels: Record<AssetStatus, string> = { unknown: '待检查', online: '在线', offline: '离线' };

