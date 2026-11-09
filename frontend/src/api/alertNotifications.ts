import { apiFetch, parseApiResponse } from './client';

export type NotificationStatus = 'pending' | 'blocked' | 'sending' | 'retry' | 'sent' | 'failed';
export type NotificationKind = 'alert_received' | 'diagnostic_result' | 'asset_mapping_failed' | 'recovery_result';
export interface AlertNotification {
  id: string; alert_id: string | null; kind: NotificationKind; status: NotificationStatus;
  title?: string; source?: string | null;
  external_id?: string | null; asset_locator?: Record<string, string>;
  attempts: number; max_attempts: number; error_code: string | null; recipients: string[];
  created_at: string; sent_at: string | null; next_attempt_at: string | null;
  acknowledged_at: string | null; acknowledged_by: string | null;
}
export interface NotificationSummary {
  diagnostic_results?: number;
  latest_diagnostic?: {
    id: string; analysis_id: string | null; status: NotificationStatus;
    error_code: string | null; acknowledged: boolean;
  } | null;
  total: number; sent: number; failed: number; blocked: number; pending: number; acknowledged: number;
}
export interface AlertNotificationConfig {
  configured: boolean; enabled: boolean; host: string; port: number; security: 'starttls' | 'ssl';
  username: string; sender: string; recipients: string[]; password_configured: boolean;
  updated_at: string | null;
}
export type AlertNotificationConfigInput = { enabled: boolean; password?: string } & Partial<Omit<AlertNotificationConfig,
  'configured' | 'enabled' | 'password_configured' | 'updated_at'>>;
export interface AlertNotificationList { total: number; items: AlertNotification[] }

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options, headers: { 'Content-Type': 'application/json', ...options?.headers },
    signal: options?.signal ?? AbortSignal.timeout(15_000),
  });
  return parseApiResponse<T>(response, '告警通知请求失败，请检查权限或稍后重试');
}
export function getAlertNotificationConfig(): Promise<AlertNotificationConfig> {
  return request('/api/v1/alert-notification-config');
}
export function saveAlertNotificationConfig(payload: AlertNotificationConfigInput): Promise<AlertNotificationConfig> {
  return request('/api/v1/alert-notification-config', { method: 'PATCH', body: JSON.stringify(payload) });
}
export function getAlertNotifications(alertId: string): Promise<{ items: AlertNotification[] }> {
  return request(`/api/v1/alerts/${encodeURIComponent(alertId)}/notifications`);
}
export function getNotificationHistory(offset = 0, limit = 50): Promise<AlertNotificationList> {
  return request(`/api/v1/alert-notifications?offset=${offset}&limit=${limit}`);
}
export function retryAlertNotification(id: string): Promise<AlertNotification> {
  return request(`/api/v1/alert-notifications/${encodeURIComponent(id)}/retry`, { method: 'POST' });
}
export function acknowledgeAlertNotification(id: string): Promise<AlertNotification> {
  return request(`/api/v1/alert-notifications/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' });
}
