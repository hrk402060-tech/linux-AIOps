import { useCallback, useEffect, useRef, useState } from 'react';
import {
  chatOperationsAssistant, createOperationsConversation, getOperationsConversation,
  getOperationsConversations, updateOperationsConversation,
  type OpsAssistantConversation, type OpsAssistantConversationSummary,
} from '../api/ops-assistant';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '会话请求失败，请稍后重试';
}

function createRequestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function useOperationsConversations() {
  const [items, setItems] = useState<OpsAssistantConversationSummary[]>([]);
  const [active, setActive] = useState<OpsAssistantConversation | null>(null);
  const [archived, setArchived] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [listError, setListError] = useState('');
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [assetId, setAssetId] = useState<string>();
  const [context, setContext] = useState('');
  const [locked, setLocked] = useState(false);
  const request = useRef({ epoch: 0, controller: new AbortController() });
  const alive = useRef(true);
  const retry = useRef<{ prompt: string; requestId: string; conversationId?: string } | null>(null);

  const begin = useCallback(() => {
    request.current.controller.abort();
    request.current = { epoch: request.current.epoch + 1, controller: new AbortController() };
    setSending(false); setDetailLoading(false); setMoreLoading(false); setError('');
    return { epoch: request.current.epoch, signal: request.current.controller.signal };
  }, []);
  const current = useCallback((epoch: number) => alive.current && epoch === request.current.epoch, []);
  const apply = useCallback((detail: OpsAssistantConversation) => {
    setActive(detail); setAssetId(detail.asset_id ?? undefined); setContext(detail.context);
    setItems((previous) => previous.map((item) => item.id === detail.id ? detail : item)
      .sort((first, second) => new Date(second.updated_at).getTime() - new Date(first.updated_at).getTime()));
  }, []);

  const open = async (id: string) => {
    const token = begin();
    setActive(null); setQuestion(''); setAssetId(undefined); setContext(''); setDetailLoading(true);
    try {
      const detail = await getOperationsConversation(id, 0, token.signal);
      if (current(token.epoch)) apply(detail);
    } catch (failure) {
      if (current(token.epoch)) setError(errorText(failure));
    } finally {
      if (current(token.epoch)) setDetailLoading(false);
    }
  };

  const reload = async (showArchived = archived, restore = false) => {
    const token = begin();
    setArchived(showArchived); setItems([]); setActive(null); setTotal(0); setQuestion('');
    setAssetId(undefined); setContext(''); setListLoading(true); setListError('');
    try {
      const list = await getOperationsConversations(showArchived, 0, token.signal);
      if (!current(token.epoch)) return;
      setItems(list.items); setTotal(list.total);
      if (restore && list.items.length) {
        setDetailLoading(true);
        const detail = await getOperationsConversation(list.items[0].id, 0, token.signal);
        if (current(token.epoch)) apply(detail);
      }
    } catch (failure) {
      if (current(token.epoch)) setListError(errorText(failure));
    } finally {
      if (current(token.epoch)) { setListLoading(false); setDetailLoading(false); }
    }
  };

  useEffect(() => {
    alive.current = true;
    void reload(false, true);
    const expire = () => {
      begin(); retry.current = null; setLocked(true); setItems([]); setActive(null); setQuestion('');
      setAssetId(undefined); setContext(''); setTotal(0); setListError(''); setListLoading(false);
    };
    window.addEventListener('sentinel:session-expired', expire);
    return () => {
      alive.current = false; request.current.controller.abort(); request.current.epoch += 1;
      window.removeEventListener('sentinel:session-expired', expire);
    };
  }, [begin]); // Each user owns a separate keyed workspace.

  const newDraft = () => {
    begin(); retry.current = null; setActive(null); setQuestion(''); setAssetId(undefined); setContext('');
    setListLoading(false);
    if (archived) { setArchived(false); void reload(false); }
  };

  const send = async () => {
    const prompt = question.trim();
    if (!prompt || sending || detailLoading || listLoading || archived || active?.archived || locked) return;
    const token = begin();
    setSending(true); setQuestion('');
    let id = active?.id;
    const requestId = retry.current?.prompt === prompt && retry.current.conversationId === id
      ? retry.current.requestId : createRequestId();
    retry.current = { prompt, requestId, conversationId: id };
    try {
      if (!id) {
        const created = await createOperationsConversation({ asset_id: assetId ?? null, context: context.trim() }, token.signal);
        if (!current(token.epoch)) return;
        id = created.id; apply(created); setItems((previous) => [created, ...previous]); setTotal((previous) => previous + 1);
        retry.current = { prompt, requestId, conversationId: id };
      }
      setActive((previous) => previous ? { ...previous, messages: [...previous.messages.filter((item) => item.id !== `pending-${requestId}`), {
        id: `pending-${requestId}`, role: 'user', content: prompt, status: 'pending',
        error_message: null, analysis: null, created_at: new Date().toISOString(),
      }] } : previous);
      await chatOperationsAssistant({ conversation_id: id, message: prompt, request_id: requestId }, token.signal);
      if (!current(token.epoch)) return;
      const detail = await getOperationsConversation(id, 0, token.signal);
      if (current(token.epoch)) { apply(detail); retry.current = null; }
    } catch (failure) {
      if (current(token.epoch)) await recoverFailed(id, prompt, failure, token);
    } finally {
      if (current(token.epoch)) setSending(false);
    }
  };

  const recoverFailed = async (id: string | undefined, prompt: string, failure: unknown, token: { epoch: number; signal: AbortSignal }) => {
    setQuestion(prompt); setError(errorText(failure));
    if (!id) return;
    try {
      const detail = await getOperationsConversation(id, 0, token.signal);
      if (current(token.epoch)) {
        apply(detail);
        const lastUser = [...detail.messages].reverse().find((item) => item.role === 'user');
        if (lastUser?.status === 'failed') retry.current = null;
      }
    } catch (refreshFailure) {
      if (current(token.epoch)) setError(`${errorText(failure)}；消息状态未能刷新：${errorText(refreshFailure)}`);
    }
  };

  const update = async (payload: { title?: string; archived?: boolean }) => {
    if (!active || sending || detailLoading || locked) return false;
    const id = active.id;
    const token = begin(); setDetailLoading(true);
    try {
      const detail = await updateOperationsConversation(id, payload, token.signal);
      if (!current(token.epoch)) return false;
      apply(detail);
      if (payload.archived !== undefined) {
        const list = await getOperationsConversations(false, 0, token.signal);
        if (!current(token.epoch)) return false;
        setArchived(false); setItems(list.items); setTotal(list.total);
        if (detail.archived) { setActive(null); setAssetId(undefined); setContext(''); setQuestion(''); }
      }
      return true;
    } catch (failure) {
      if (current(token.epoch)) setError(errorText(failure));
      return false;
    } finally {
      if (current(token.epoch)) setDetailLoading(false);
    }
  };

  const loadMoreMessages = async () => {
    if (!active || moreLoading || sending || detailLoading || locked) return;
    const token = request.current; const id = active.id; setMoreLoading(true);
    try {
      const detail = await getOperationsConversation(id, active.messages.length, token.controller.signal);
      if (!current(token.epoch)) return;
      setActive((previous) => previous?.id === id ? {
        ...previous, total_messages: detail.total_messages,
        messages: [...detail.messages, ...previous.messages].filter((item, index, all) => all.findIndex(({ id: messageId }) => messageId === item.id) === index),
      } : previous);
    } catch (failure) {
      if (current(token.epoch)) setError(errorText(failure));
    } finally {
      if (current(token.epoch)) setMoreLoading(false);
    }
  };

  const loadMoreConversations = async () => {
    if (listLoading || moreLoading || locked) return;
    const token = request.current; setMoreLoading(true);
    try {
      const list = await getOperationsConversations(archived, items.length, token.controller.signal);
      if (current(token.epoch)) { setItems((previous) => [...previous, ...list.items]); setTotal(list.total); }
    } catch (failure) {
      if (current(token.epoch)) setError(errorText(failure));
    } finally {
      if (current(token.epoch)) setMoreLoading(false);
    }
  };

  return {
    items, active, archived, total, listLoading, detailLoading, sending, moreLoading,
    listError, error, question, setQuestion, assetId, setAssetId, context, setContext, locked,
    open, reload, newDraft, send, update, loadMoreMessages, loadMoreConversations,
  };
}
