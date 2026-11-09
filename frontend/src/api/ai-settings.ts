import { apiFetch, parseApiResponse } from './client';

export interface TerminalAISettings {
  enabled: boolean;
  base_url: string;
  model: string;
  timeout_seconds: number;
  api_key_masked: string | null;
  updated_at: string | null;
}
export interface TerminalAISettingsUpdate {
  enabled: boolean;
  base_url: string;
  model: string;
  timeout_seconds: number;
  api_key: string | null;
  clear_api_key: boolean;
}
const endpoint = '/api/v1/agent/settings/terminal';
export async function getTerminalAISettings(): Promise<TerminalAISettings> {
  return parseApiResponse<TerminalAISettings>(await apiFetch(endpoint), '运维 AI 配置加载失败');
}
export async function saveTerminalAISettings(payload: TerminalAISettingsUpdate): Promise<TerminalAISettings> {
  return parseApiResponse<TerminalAISettings>(await apiFetch(endpoint, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }), '运维 AI 配置保存失败');
}
export function isValidTerminalAIUrl(value: string): boolean {
  if (/[\s\\?#]/.test(value) || /[\u0000-\u001f\u007f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && Boolean(url.hostname)
      && !url.username && !url.password && url.port !== '0';
  } catch { return false; }
}
