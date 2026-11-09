export type CredentialRiskStatus = 'queued' | 'assessing' | 'aggregating' | 'completed' | 'failed' | 'cancelled';
export interface CredentialRiskResult {
  credential_id: string; credential_name: string; username: string;
  risk_level: 'high' | 'medium' | 'low'; status: 'at_risk' | 'passed'; violations: string[];
}
export interface CredentialRiskTask {
  id: string; name: string; status: CredentialRiskStatus; progress: number; offline_only: boolean;
  policy_version: string; credential_ids: string[];
  credential_snapshot: Array<{ id: string; name: string; username: string; asset_count: number }>;
  summary: { credentials_total: number; at_risk: number; high: number; medium: number; passed: number } | null;
  results: CredentialRiskResult[]; execution_log: Array<{ status: CredentialRiskStatus; message: string; occurred_at: string }>;
  error_message: string | null; rerun_of_id: string | null; started_at: string | null; finished_at: string | null; created_at: string; updated_at: string;
}
