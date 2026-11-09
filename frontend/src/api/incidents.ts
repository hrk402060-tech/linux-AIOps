import { apiFetch, parseApiResponse } from './client';

export type IncidentSeverity = 'critical' | 'high' | 'medium' | 'low';
export type IncidentStatus = 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'closed';
export type IncidentSlaStatus = 'on_track' | 'at_risk' | 'breached' | 'met';
export type IncidentTicketStatus = 'open' | 'in_progress' | 'resolved' | 'closed';

export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  asset_id: string;
  asset_name: string;
  alert_id: string | null;
  task_id: string | null;
  operator: string;
  created_at: string;
  updated_at?: string;
  sla_target_minutes?: number;
  sla_due_at?: string | null;
  sla_status?: IncidentSlaStatus;
}

export interface IncidentTimelineEntry {
  status: IncidentStatus;
  note: string;
  operator: string;
  created_at: string;
}

export interface IncidentList {
  total: number;
  items: Incident[];
}

export interface IncidentTicket {
  id: string;
  incident_id: string;
  title: string;
  description: string;
  priority: IncidentSeverity;
  status: IncidentTicketStatus;
  assignee_user_id: string | null;
  assignee_username: string | null;
  due_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  sla_status: IncidentSlaStatus;
}

export interface IncidentCreate {
  title: string;
  description: string;
  severity: IncidentSeverity;
  asset_id: string;
  alert_id?: string;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    signal: options?.signal ?? AbortSignal.timeout(15_000),
  });
  return parseApiResponse<T>(response, '故障请求失败，请检查权限或稍后重试');
}

export function getIncidents(filters: Record<string, string>): Promise<IncidentList> {
  const query = new URLSearchParams(filters).toString();
  return request<IncidentList>(`/api/v1/incidents${query ? `?${query}` : ''}`);
}

export function getIncident(id: string): Promise<Incident> {
  return request<Incident>(`/api/v1/incidents/${encodeURIComponent(id)}`);
}

export function createIncident(payload: IncidentCreate): Promise<Incident> {
  return request<Incident>('/api/v1/incidents', {
    method: 'POST', body: JSON.stringify(payload),
  });
}

export function advanceIncident(
  id: string, status: IncidentStatus, note: string,
): Promise<Incident> {
  return request<Incident>(`/api/v1/incidents/${encodeURIComponent(id)}/status`, {
    method: 'PATCH', body: JSON.stringify({ status, note }),
  });
}

export function getIncidentTimeline(id: string): Promise<IncidentTimelineEntry[]> {
  return request<IncidentTimelineEntry[]>(
    `/api/v1/incidents/${encodeURIComponent(id)}/timeline`,
  );
}

export function getIncidentTickets(id: string): Promise<IncidentTicket[]> {
  return request<IncidentTicket[]>(`/api/v1/incidents/${encodeURIComponent(id)}/tickets`);
}

export function createIncidentTicket(
  id: string,
  payload: {
    title: string;
    description: string;
    priority: IncidentSeverity;
    assignee_user_id?: string;
    due_at?: string;
  },
): Promise<IncidentTicket> {
  return request<IncidentTicket>(`/api/v1/incidents/${encodeURIComponent(id)}/tickets`, {
    method: 'POST', body: JSON.stringify(payload),
  });
}

export function updateIncidentTicket(
  id: string,
  status: IncidentTicketStatus,
  note: string,
): Promise<IncidentTicket> {
  return request<IncidentTicket>(`/api/v1/incidents/tickets/${encodeURIComponent(id)}/status`, {
    method: 'PATCH', body: JSON.stringify({ status, note }),
  });
}
