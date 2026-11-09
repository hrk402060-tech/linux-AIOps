import { apiFetch, parseApiResponse } from './client';

export interface OpsAssistantChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface OpsAssistantReadOnlyCheck {
  command: string;
  purpose: string;
  validation_step: string;
}

export interface OpsAssistantChatResponse {
  conversation_id: string;
  engine: string;
  reply: string;
  diagnosis: string;
  plan: string[];
  validation_steps: string[];
  read_only: true;
  context_sources: string[];
  read_only_checks: OpsAssistantReadOnlyCheck[];
  history: OpsAssistantChatMessage[];
}

export interface OpsAssistantChatRequest {
  message: string;
  request_id: string;
  conversation_id?: string | null;
}

export interface OpsAssistantConversationSummary {
  id: string;
  title: string;
  asset_id: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface OpsAssistantMessage extends OpsAssistantChatMessage {
  id: string;
  status: 'completed' | 'pending' | 'failed';
  error_message: string | null;
  analysis: OpsAssistantChatResponse | null;
  created_at: string;
}

export interface OpsAssistantConversation extends OpsAssistantConversationSummary {
  context: string;
  messages: OpsAssistantMessage[];
  total_messages: number;
}

export interface OpsAssistantConversationList {
  items: OpsAssistantConversationSummary[];
  total: number;
}

const endpoint = '/api/v1/ops-assistant';

async function request<T>(
  path: string, init: RequestInit = {}, signal?: AbortSignal, timeout = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = window.setTimeout(abort, timeout);
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await apiFetch(endpoint + path, { ...init, signal: controller.signal });
    return await parseApiResponse<T>(response, 'AI 运维助手请求失败');
  } catch (failure) {
    if (controller.signal.aborted && !signal?.aborted) throw new Error('请求超时，已保留问题，请稍后重试');
    throw failure;
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

function json(method: string, payload: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
}

export function chatOperationsAssistant(payload: OpsAssistantChatRequest, signal?: AbortSignal): Promise<OpsAssistantChatResponse> {
  return request('/chat', json('POST', payload), signal, 150_000);
}

export function getOperationsConversations(archived = false, offset = 0, signal?: AbortSignal): Promise<OpsAssistantConversationList> {
  return request(`/conversations?archived=${archived}&offset=${offset}&limit=30`, {}, signal);
}

export function createOperationsConversation(
  payload: { asset_id?: string | null; context?: string }, signal?: AbortSignal,
): Promise<OpsAssistantConversation> {
  return request('/conversations', json('POST', payload), signal);
}

export function getOperationsConversation(id: string, offset = 0, signal?: AbortSignal): Promise<OpsAssistantConversation> {
  return request(`/conversations/${encodeURIComponent(id)}?offset=${offset}&limit=100`, {}, signal);
}

export function updateOperationsConversation(
  id: string, payload: { title?: string; archived?: boolean }, signal?: AbortSignal,
): Promise<OpsAssistantConversation> {
  return request(`/conversations/${encodeURIComponent(id)}`, json('PATCH', payload), signal);
}
