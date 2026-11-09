export type ReportFormat = 'pdf' | 'docx';
export type ReportStatus = 'queued' | 'generating' | 'completed' | 'failed';

export interface ReportAudit {
  action: string;
  operator: string;
  detail: string;
  occurred_at: string;
}

export interface ScanReport {
  id: string;
  scan_task_id: string;
  scan_task_name: string;
  format: ReportFormat;
  status: ReportStatus;
  asset_snapshot: Array<{ id?: string; name?: string; host?: string }>;
  knowledge_version: string | null;
  vulnerability_summary: Record<string, number>;
  finding_snapshot: Array<Record<string, unknown>>;
  file_name: string | null;
  file_size: number | null;
  sha256: string | null;
  error_message: string | null;
  download_count: number;
  generated_by: string;
  audit_history: ReportAudit[];
  created_at: string;
  completed_at: string | null;
}

export interface ReportCreate {
  scan_task_id: string;
  format: ReportFormat;
}
