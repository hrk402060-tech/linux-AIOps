export type ExposureStatus = 'queued' | 'scanning' | 'aggregating' | 'completed' | 'failed' | 'cancelled';

export interface ExposureListener {
  protocol: string; listen_host: string; port: number | null; process: string;
  scope: 'all_interfaces' | 'specific_interface' | 'loopback';
  conclusion: string; evidence_source: string;
}
export interface ExposureAssetResult {
  asset_id: string; asset_name: string; status: string; listeners: ExposureListener[]; message?: string;
}
export interface ExposureTask {
  id: string; name: string; status: ExposureStatus; progress: number; read_only: boolean;
  methodology_version: string; asset_ids: string[];
  asset_snapshot: Array<{ id: string; name: string; host: string; os_family: string }>;
  summary: { assets_total: number; assets_checked: number; listeners: number; potential_exposures: number; loopback_only: number } | null;
  results: ExposureAssetResult[]; execution_log: Array<{ status: ExposureStatus; message: string; occurred_at: string }>;
  error_message: string | null; rerun_of_id: string | null; started_at: string | null;
  finished_at: string | null; created_at: string; updated_at: string;
}
