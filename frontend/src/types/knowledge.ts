export interface KnowledgeSummary {
  active_version: string | null;
  advisory_count: number;
  source_count: number;
  domestic_source_count: number;
  last_updated_at: string | null;
}

export interface KnowledgeCoverageSource {
  source_id: string;
  source_name: string;
  provider: string;
  os_families: string[];
  sync_mode: 'offline_package' | 'online_secondary' | 'vendor_official_api';
  advisory_count: number;
  latest_advisory_at: string | null;
  last_success_at: string | null;
  latest_sync_status: string | null;
  health: 'healthy' | 'stale' | 'failed' | 'never_synced';
  is_stale: boolean;
  has_alert: boolean;
  stale_after_hours: number;
  daily_schedule: string | null;
  critical_check_interval_hours: number | null;
}

export interface KnowledgeCoverage {
  is_complete: false;
  completeness_note: string;
  publication_policy: 'stage_validate_publish';
  failure_policy: 'retain_active_release_and_alert';
  critical_check_interval_hours: number;
  sources: KnowledgeCoverageSource[];
}

export interface AffectedPackage {
  name: string;
  affected_version: string;
  fixed_version: string;
  os_versions: string[];
}

export interface KnowledgeAdvisory {
  id: string;
  cve_id: string;
  advisory_id: string;
  title: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'unknown';
  os_family: string;
  source_name: string;
  vendor_url: string;
  affected_packages: AffectedPackage[];
  published_at: string;
  updated_at: string;
}

export interface KnowledgeSource {
  id: string;
  name: string;
  provider: string;
  sync_mode: 'offline_package' | 'online_secondary' | 'vendor_official_api';
  priority: number;
  enabled: boolean;
  online_url: string | null;
  schedule: string | null;
  last_sync_at: string | null;
}

export interface KnowledgeRelease {
  id: string;
  version: string;
  name: string;
  package_id: string;
  source_name: string;
  signature_key_id: string;
  content_sha256: string;
  advisory_count: number;
  status: 'active' | 'archived';
  created_at: string;
  activated_at: string | null;
}

export interface KnowledgeSyncRun {
  id: string;
  source_id: string | null;
  source_name: string;
  mode: string;
  status: 'running' | 'completed' | 'failed';
  filename: string | null;
  signature_key_id: string | null;
  content_sha256: string | null;
  advisory_count: number;
  message: string;
  started_at: string;
  finished_at: string | null;
}

export interface KnowledgeImportPayload {
  filename: string;
  package_content: string;
  signature: string;
  key_id: string;
}

export interface KnowledgeBackfill {
  id: string;
  source_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  current_page: number;
  total_pages: number | null;
  batch_size: number;
  delay_seconds: number;
  processed_notices: number;
  published_advisories: number;
  progress: number;
  error_message: string | null;
  last_release_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBackfillPayload {
  batch_size: number;
  delay_seconds: number;
}
