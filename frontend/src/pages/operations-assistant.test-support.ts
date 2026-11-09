import { vi } from 'vitest';
import type { OpsAssistantChatResponse, OpsAssistantConversation, OpsAssistantMessage } from '../api/ops-assistant';

export const response = (body: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
}) as Response;

export const answer: OpsAssistantChatResponse = {
  conversation_id: 'created-1', engine: 'local-rules', reply: '建议先核对负载与内存回收情况。',
  diagnosis: '需要补充只读指标。', plan: ['查看负载趋势'], validation_steps: ['比较指标趋势'],
  read_only: true, context_sources: ['用户补充证据'], history: [],
  read_only_checks: [{ command: 'uptime', purpose: '查看负载', validation_step: '确认负载下降' }],
};
export const turn = (id: string, content: string): OpsAssistantMessage => ({
  id, role: 'user', content, status: 'completed', error_message: null, analysis: null, created_at: '2026-10-21T04:00:00Z',
});
export const conversation = (id: string, title: string, messages: OpsAssistantMessage[] = []): OpsAssistantConversation => ({
  id, title, asset_id: null, archived: false, created_at: '2026-10-21T04:00:00Z',
  updated_at: '2026-10-21T05:00:00Z', context: '', messages, total_messages: messages.length,
});

export function installConversationApi(initial: OpsAssistantConversation[] = []) {
  const state: { conversations: OpsAssistantConversation[]; failChat: boolean; chatPromise?: Promise<Response>; detailPromise?: Promise<Response> } = {
    conversations: initial, failChat: false,
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname.includes('/assets')) return response([]);
    const payload = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    if (url.pathname.endsWith('/chat')) return processChat(state, payload);
    if (url.pathname.endsWith('/conversations')) {
      if (init?.method === 'POST') {
        const created = { ...conversation('created-1', '新会话'), asset_id: payload.asset_id as string | null, context: String(payload.context ?? '') };
        state.conversations.unshift(created);
        return response(created, 201);
      }
      const archived = url.searchParams.get('archived') === 'true';
      const filtered = state.conversations.filter((item) => item.archived === archived);
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return response({ items: filtered.slice(offset, offset + 30), total: filtered.length });
    }
    if (state.detailPromise && url.pathname.includes('/conversations/previous')) return state.detailPromise;
    const item = state.conversations.find(({ id }) => url.pathname.endsWith(`/${id}`));
    if (!item) return response({ detail: '会话不存在' }, 404);
    if (init?.method === 'PATCH') Object.assign(item, payload);
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 100);
    return response({ ...item, messages: [...item.messages].reverse().slice(offset, offset + limit).reverse(), total_messages: item.messages.length });
  }));
  return state;
}

function processChat(
  state: { conversations: OpsAssistantConversation[]; failChat: boolean; chatPromise?: Promise<Response>; detailPromise?: Promise<Response> },
  payload: Record<string, unknown>,
): Response | Promise<Response> {
  if (state.chatPromise) return state.chatPromise;
  const item = state.conversations.find(({ id }) => id === payload.conversation_id);
  if (!item) return response({ detail: '会话不存在' }, 404);
  const user = turn(`u${item.messages.length}`, String(payload.message));
  if (state.failChat) {
    user.status = 'failed'; user.error_message = '模型服务暂时不可用'; item.messages.push(user);
    return response({ detail: '模型服务暂时不可用' }, 503);
  }
  item.messages.push(user, { ...turn(`a${item.messages.length}`, answer.reply), role: 'assistant', analysis: answer });
  return response({ ...answer, conversation_id: item.id });
}
