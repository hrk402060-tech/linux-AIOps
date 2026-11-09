import { apiFetch } from './client';
import type {
  TerminalAssistantAnalysis, TerminalAssistantChatMessage, TerminalAssistantChatResponse, TerminalAssistantCommand, TerminalAssistantMode,
  TerminalSessionSummary, TerminalTicket,
} from '../types/terminal';

const endpoint = '/api/v1/terminal';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? '终端请求失败');
  }
  return (await response.json()) as T;
}

export function createTerminalTicket(assetId: string): Promise<TerminalTicket> {
  return request<TerminalTicket>(endpoint + '/tickets', {
    method: 'POST', body: JSON.stringify({ asset_id: assetId }),
  });
}

export function getTerminalSessions(limit = 20): Promise<TerminalSessionSummary[]> {
  return request<TerminalSessionSummary[]>(`${endpoint}/sessions?limit=${limit}`);
}

export function disconnectTerminalSession(sessionId: string): Promise<{ status: string }> {
  return request<{ status: string }>(endpoint + '/sessions/' + sessionId + '/disconnect', {
    method: 'POST',
  });
}

export function analyzeTerminal(
  sessionId: string, mode: TerminalAssistantMode, context: string,
): Promise<TerminalAssistantAnalysis> {
  return request<TerminalAssistantAnalysis>(endpoint + '/assistant/analyze', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, mode, context }),
  });
}

export function reviewTerminalCommand(
  commandId: string, decision: 'approved' | 'rejected', confirmation: string,
): Promise<TerminalAssistantCommand> {
  return request<TerminalAssistantCommand>(endpoint + '/assistant/commands/' + commandId + '/review', {
    method: 'PATCH', body: JSON.stringify({ decision, confirmation }),
  });
}
export function chatTerminalAssistant(
  sessionId: string, mode: TerminalAssistantMode, message: string,
  context: string, history: TerminalAssistantChatMessage[],
): Promise<TerminalAssistantChatResponse> {
  return request<TerminalAssistantChatResponse>(endpoint + '/assistant/chat', {
    signal: AbortSignal.timeout(90_000),
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, mode, message, context, history }),
  });
}
