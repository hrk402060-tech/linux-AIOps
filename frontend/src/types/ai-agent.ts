import type { VulnerabilitySeverity } from './vulnerability';

export type AgentRecommendationStatus = 'pending_review' | 'approved' | 'rejected';
export type AgentPriority = 'urgent' | 'high' | 'normal' | 'review';

export interface AgentProvider {
  id: string;
  name: string;
  kind: 'local' | 'external';
  status: 'ready' | 'disabled';
  requires_api_key: boolean;
  configured?: boolean;
  enabled?: boolean;
  api_key_masked?: string | null;
}

export interface AgentFinding {
  id: string;
  asset_id: string;
  disposition_status: 'open' | 'pending_verification';
  cve_id: string;
  title: string;
  asset_name: string;
  severity: VulnerabilitySeverity;
  conclusion: string;
  confidence: string;
  package_name: string;
  installed_version: string;
  fixed_version: string;
  source_name: string;
}

export interface AgentRecommendation {
  id: string;
  finding_id: string;
  cve_id: string;
  asset_name: string;
  provider_id: string;
  provider_name: string;
  status: AgentRecommendationStatus;
  priority: AgentPriority;
  risk_analysis: string;
  remediation_steps: string[];
  validation_steps: string[];
  rollback_plan: string;
  evidence_snapshot: Array<{ type: string; value: string }>;
  created_at: string;
  reviewed_at: string | null;
  reviewed_by: string | null;
  can_execute: false;
}

export interface AgentWorkspace {
  providers: AgentProvider[];
  summary: {
    pending_findings: number;
    pending_review: number;
    approved: number;
    urgent_alerts: number;
  };
  findings: AgentFinding[];
  recommendations: AgentRecommendation[];
  queue_total: number;
  queue_page: number;
  history_total: number;
  history_page: number;
}
