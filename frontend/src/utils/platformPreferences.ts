import type { PlatformPreferences } from '../api/system';

export const defaultPlatformPreferences: PlatformPreferences = {
  platform_name: 'Linux 智能综合运维平台', timezone: 'Asia/Shanghai',
  display_density: 'comfortable', default_page: 'dashboard',
};
let timezone = defaultPlatformPreferences.timezone;

export function applyPlatformPreferences(preferences: PlatformPreferences): void {
  timezone = preferences.timezone;
  document.title = preferences.platform_name;
  document.documentElement.dataset.density = preferences.display_density;
  document.documentElement.dataset.timezone = preferences.timezone;
}
export function platformDateTimeOptions(options: Intl.DateTimeFormatOptions = {}): Intl.DateTimeFormatOptions {
  return { ...options, timeZone: timezone };
}
export function formatPlatformDateTime(
  value: string | number | Date | null | undefined, options: Intl.DateTimeFormatOptions = {},
): string {
  return value !== null && value !== undefined && value !== ''
    ? (value instanceof Date ? value : new Date(value)).toLocaleString('zh-CN', platformDateTimeOptions(options)) : '—';
}
