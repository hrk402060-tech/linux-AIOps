import { apiFetch } from './client';
import type { AgentRecommendation, AgentWorkspace } from '../types/ai-agent';
import type { AgentProvider } from '../types/ai-agent';

const endpoint = '/api/v1/agent';

const headers = { 'Content-Type': 'application/json' };

async function parse<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? fallback);
  }
  return (await response.json()) as T;
}

export interface AgentWorkspacePages {
  queuePage?: number;
  queuePageSize?: number;
  historyPage?: number;
  historyPageSize?: number;
}

export async function getAgentWorkspace(pages: AgentWorkspacePages = {}): Promise<AgentWorkspace> {
  const query = new URLSearchParams();
  if (pages.queuePage !== undefined) query.set('queue_page', String(pages.queuePage));
  if (pages.queuePageSize !== undefined) query.set('queue_page_size', String(pages.queuePageSize));
  if (pages.historyPage !== undefined) query.set('history_page', String(pages.historyPage));
  if (pages.historyPageSize !== undefined) query.set('history_page_size', String(pages.historyPageSize));
  const suffix = query.size ? '?' + query.toString() : '';
  return parse<AgentWorkspace>(
    await apiFetch(endpoint + suffix, { headers }), 'AI Agent 数据加载失败',
  );
}

export async function getProviderConfigs(): Promise<AgentProvider[]> {
  return parse<AgentProvider[]>(await apiFetch(endpoint + '/providers/config', { headers }), '模型配置加载失败');
}

export async function saveProviderConfig(providerId: string, apiKey: string, enabled: boolean): Promise<{ provider_id: string; enabled: boolean; api_key_masked: string }> {
  return parse(await apiFetch(endpoint + '/providers/config', { method: 'POST', headers, body: JSON.stringify({ provider_id: providerId, api_key: apiKey, enabled }) }), '模型配置保存失败');
}

export async function setProviderEnabled(providerId: string, enabled: boolean): Promise<{ provider_id: string; enabled: boolean }> {
  return parse(await apiFetch(endpoint + '/providers/config/' + providerId + '/enabled', { method: 'PATCH', headers, body: JSON.stringify({ enabled }) }), '模型状态更新失败');
}

export async function validateProviderConfig(providerId: string): Promise<{ configured: boolean; network_called: boolean }> {
  return parse(await apiFetch(endpoint + '/providers/config/' + providerId + '/validate', { method: 'POST', headers, body: JSON.stringify({}) }), '模型密钥校验失败');
}

export async function createAgentTriage(
  findingId: string, providerId: string,
): Promise<AgentRecommendation> {
  return parse<AgentRecommendation>(await apiFetch(endpoint + '/triage', {
    method: 'POST', headers,
    body: JSON.stringify({ finding_id: findingId, provider_id: providerId }),
  }), '研判建议生成失败');
}

export async function reviewAgentRecommendation(
  recommendationId: string, decision: 'approved' | 'rejected',
): Promise<AgentRecommendation> {
  return parse<AgentRecommendation>(
    await apiFetch(endpoint + '/recommendations/' + recommendationId + '/review', {
      method: 'PATCH', headers, body: JSON.stringify({ decision }),
    }), '建议审核失败',
  );
}
