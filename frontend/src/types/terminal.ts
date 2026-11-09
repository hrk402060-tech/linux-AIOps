export type TerminalSessionStatus =
  | 'waiting' | 'connected' | 'closed' | 'failed' | 'terminated' | 'timeout' | 'revoked';

export interface TerminalTicket {
  ticket: string;
  session_id: string;
  websocket_path: string;
  expires_at: string;
}

export interface TerminalConnectionDetails {
  reason?: string;
  message?: string;
  ended_at?: string;
}

export interface TerminalSessionSummary {
  id: string;
  asset_id: string;
  asset_name: string;
  host: string;
  operator: string;
  status: TerminalSessionStatus;
  requested_at: string;
  connected_at: string | null;
  ended_at: string | null;
  failure_reason?: string | null;
}

// Keep the historical name as a compatibility alias for API consumers.
export type TerminalSession = TerminalSessionSummary;

export type TerminalAssistantMode = 'analysis_only' | 'assisted' | 'controlled';
export type TerminalCommandStatus =
  | 'suggested' | 'pending_confirmation' | 'approved' | 'rejected'
  | 'executing' | 'executed' | 'failed';

export interface TerminalAssistantCommand {
  id: string; analysis_id: string; terminal_session_id: string; command: string;
  purpose: string; validation_step: string; risk_level: 'low' | 'medium' | 'high';
  status: TerminalCommandStatus; confirmation_phrase: string; requested_by: string;
  reviewed_by: string | null; reviewed_at: string | null; executed_at: string | null;
  created_at: string;
}

export interface TerminalAssistantAnalysis {
  id: string; terminal_session_id: string; mode: TerminalAssistantMode;
  redacted_context: string; diagnosis: string; plan: string[];
  validation_steps: string[]; operator: string; created_at: string;
  commands: TerminalAssistantCommand[];
}
export interface TerminalAssistantChatMessage { role: 'user' | 'assistant'; content: string; }

export interface TerminalAssistantChatResponse {
  conversation_id: string; engine: string; reply: string;
  analysis: TerminalAssistantAnalysis; history: TerminalAssistantChatMessage[];
}
