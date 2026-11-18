import { apiFetch, parseApiResponse } from './client';

export interface SettingsConnectionTest { ok: boolean; detail: string; checked_at: string }
export interface WebhookChannel {
  enabled: boolean; url_masked: string | null; token_configured: boolean; updated_at: string | null;
}
export interface WebhookChannelInput { enabled: boolean; url?: string; token?: string; clear_token?: boolean }
const endpoint = '/api/v1/system/notification-channel';
export async function getWebhookChannel(): Promise<WebhookChannel> {
  return parseApiResponse(await apiFetch(endpoint), '消息渠道加载失败');
}
export async function saveWebhookChannel(payload: WebhookChannelInput): Promise<WebhookChannel> {
  return parseApiResponse(await apiFetch(endpoint, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }), '消息渠道保存失败');
}
export async function testSettingsChannel(channel: 'email' | 'webhook'): Promise<SettingsConnectionTest> {
  return parseApiResponse(await apiFetch(channel === 'email' ? '/api/v1/alert-notification-config/test' : `${endpoint}/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    signal: AbortSignal.timeout(40_000),
  }), '通知测试失败');
}
