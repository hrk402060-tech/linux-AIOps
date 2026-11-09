export type BaselineStatus = 'queued' | 'scanning' | 'aggregating' | 'completed' | 'failed' | 'cancelled';

export interface BaselineCheck {
  rule_id: string; title: string; severity: string; status: 'passed' | 'failed';
  observed: string; expected: string; evidence_source: string;
}
export interface BaselineAssetResult {
  asset_id: string; asset_name: string; status: string; checks: BaselineCheck[]; message?: string;
}
export interface BaselineTask {
  id: string; name: string; status: BaselineStatus; progress: number; read_only: boolean;
  policy_id: string; policy_version: string; asset_ids: string[];
  asset_snapshot: Array<{ id: string; name: string; host: string; os_family: string }>;
  summary: { assets_total: number; assets_checked: number; passed: number; failed: number; compliance_rate: number } | null;
  results: BaselineAssetResult[]; execution_log: Array<{ status: BaselineStatus; message: string; occurred_at: string }>;
  error_message: string | null; rerun_of_id: string | null; started_at: string | null;
  finished_at: string | null; created_at: string; updated_at: string;
}
