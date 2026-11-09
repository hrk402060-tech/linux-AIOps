import type { CredentialType } from './credential';

export type AssetStatus = 'unknown' | 'online' | 'offline';
export type AssetLifecycleStatus = 'enabled' | 'disabled' | 'archived';
export type AssetEnvironment = 'production' | 'staging' | 'test' | 'development' | 'other';
export type OsFamily =
  | 'auto' | 'ubuntu' | 'debian' | 'rhel' | 'centos' | 'rocky' | 'almalinux'
  | 'openeuler' | 'kylin' | 'uos' | 'anolis' | 'other';

export interface AssetCredential {
  id: string;
  name: string;
  username: string;
  credential_type: CredentialType;
}

export interface Asset {
  id: string;
  name: string;
  host: string;
  ssh_port: number;
  os_family: OsFamily;
  environment: AssetEnvironment;
  group_name: string;
  credential_id: string | null;
  credential: AssetCredential | null;
  labels: string[];
  status: AssetStatus;
  lifecycle_status: AssetLifecycleStatus;
  lifecycle_reason: string | null;
  lifecycle_operator: string | null;
  archived_at: string | null;
  latency_ms: number | null;
  check_message: string | null;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetCreate {
  name: string;
  host: string;
  ssh_port: number;
  os_family: OsFamily;
  environment: AssetEnvironment;
  group_name: string;
  credential_id: string | null;
  ssh_username?: string;
  ssh_credential_type?: 'password' | 'private_key';
  ssh_secret?: string;
  labels: string[];
}

export type AssetUpdate = Partial<AssetCreate>;

export interface AssetCleanupPreview {
  asset_id: string;
  allowed: boolean;
  reason: string | null;
  confirmation_phrase: string;
  scan_tasks: number;
  shared_scan_tasks: number;
  vulnerability_findings: number;
  vulnerability_cases: number;
  reports: number;
  monitoring_records: number;
  terminal_sessions: number;
  agent_recommendations: number;
}
export interface SshHostKeyProbe {
  asset_id: string;
  host: string;
  port: number;
  key_type: string;
  fingerprint: string;
  public_key: string;
  probed_at: string;
}

export interface SshHostKeyRegistration {
  asset_id: string;
  registered: boolean;
  host: string;
  port: number;
  key_type: string | null;
  fingerprint: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
}
