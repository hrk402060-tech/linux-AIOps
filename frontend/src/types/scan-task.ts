export type ScanType = 'host_vulnerability' | 'service_vulnerability' | 'comprehensive';
export type ScanStatus = 'queued' | 'scanning' | 'aggregating' | 'completed' | 'failed' | 'cancelled';

export interface ScanAssetSnapshot {
  id: string;
  name: string;
  host: string;
  ssh_port: number;
  os_family: string;
  environment: string;
  group_name: string;
  credential_configured: boolean;
}

export interface ScanLogEntry {
  status: ScanStatus;
  message: string;
  occurred_at: string;
}

export interface ScanResultSummary {
  assets_total: number;
  assets_scanned: number;
  assets_failed: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  manual_confirmation: number;
  evidence_policy: string;
  collection_mode: string;
  asset_results?: ScanAssetResult[];
}

export interface ScanServiceEvidence {
  protocol: string;
  listen: string;
  process: string;
  process_name: string;
  pid: string;
  executable: string;
  package_name: string;
  installed_version: string;
  banner?: string;
}

export interface ScanAssetResult {
  asset_id?: string;
  asset_name: string;
  status: string;
  service_count?: number;
  services?: ScanServiceEvidence[];
}

export interface ScanTask {
  id: string;
  name: string;
  scan_type: ScanType;
  status: ScanStatus;
  progress: number;
  read_only: boolean;
  asset_ids: string[];
  asset_snapshot: ScanAssetSnapshot[];
  knowledge_release_id: string | null;
  knowledge_version: string | null;
  rerun_of_id: string | null;
  schedule_id: string | null;
  result_summary: ScanResultSummary | null;
  findings: Record<string, unknown>[];
  execution_log: ScanLogEntry[];
  error_message: string | null;
  archived: boolean;
  archived_at: string | null;
  archived_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScanSchedule {
  id: string; name: string; scan_type: ScanType; asset_ids: string[];
  asset_snapshot: ScanAssetSnapshot[]; schedule_kind: 'daily' | 'interval';
  daily_time: string | null; interval_hours: number | null; timezone: string; enabled: boolean;
  last_run_at: string | null; next_run_at: string; last_task_id: string | null;
  created_at: string; updated_at: string;
}

export interface ScanScheduleCreate {
  name: string; scan_type: ScanType; asset_ids: string[];
  schedule_kind: 'daily' | 'interval'; daily_time?: string; interval_hours?: number;
  timezone: 'Asia/Shanghai';
}

export interface ScanTaskCreate {
  name: string;
  scan_type: ScanType;
  asset_ids: string[];
}
