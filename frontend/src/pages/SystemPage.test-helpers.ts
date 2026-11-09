export const auditor = {
  id: 'audit-1', username: 'audit-user', display_name: '审计用户',
  role: 'auditor' as const, enabled: true, must_change_password: false,
  created_at: '2026-09-15T00:00:00Z', last_login_at: null,
};

export const terminalAI = {
  enabled: false, base_url: '', model: '', timeout_seconds: 30,
  api_key_masked: null, updated_at: null,
};

export const asset = {
  id: 'asset-1', name: '核心服务器', host: '10.0.0.1', ssh_port: 22,
  os_family: 'openeuler', environment: 'production', group_name: '核心',
  credential_id: null, credential: null, labels: [], status: 'online',
  lifecycle_status: 'enabled', lifecycle_reason: null, lifecycle_operator: null,
  archived_at: null, latency_ms: 4, check_message: null, last_checked_at: null,
  created_at: '2026-09-15T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
};

export const systemStatus = {
  database_status: 'ok', database_engine: 'postgresql', schema_revision: '20260918_24',
  monitoring_interval_seconds: 300, knowledge_sync_interval_seconds: 14400,
  terminal: {
    max_connections: 4, idle_timeout_seconds: 900,
    max_duration_seconds: 14400, host_key_verification: true,
  },
  security: {
    scan_read_only_default: true, ai_direct_execution: false,
    credential_encryption: 'AES-256-GCM', authentication: 'password_session',
  },
  checked_at: '2026-09-18T00:00:00Z',
};
