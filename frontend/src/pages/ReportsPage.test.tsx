import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReportsPage } from './ReportsPage';

const report = {
  id: 'report-1', scan_task_id: 'scan-1', scan_task_name: '生产环境漏洞扫描',
  format: 'pdf', status: 'completed', asset_snapshot: [{ id: 'asset-1', name: 'web-01', host: '10.0.0.8' }],
  knowledge_version: 'openeuler-20260905', vulnerability_summary: { critical: 1, high: 2, medium: 0, low: 0, manual_confirmation: 1 },
  finding_snapshot: [{ asset_id: 'asset-1', asset_name: 'web-01', cve_id: 'CVE-2026-1001', severity: 'high', package_name: 'openssl', installed_version: '1.0', disposition_status: 'open' }],
  file_name: 'report.pdf', file_size: 2048, sha256: 'a'.repeat(64), error_message: null,
  download_count: 0, generated_by: 'admin', created_at: '2026-09-07T08:00:00Z',
  completed_at: '2026-09-07T08:00:02Z', audit_history: [],
};
const scan = {
  id: 'scan-1', name: '生产环境漏洞扫描', scan_type: 'comprehensive', status: 'completed',
  progress: 100, read_only: true, asset_ids: ['asset-1'], asset_snapshot: report.asset_snapshot,
  knowledge_release_id: 'release-1', knowledge_version: 'openeuler-20260905', rerun_of_id: null,
  result_summary: report.vulnerability_summary, findings: [], execution_log: [], error_message: null,
  started_at: '2026-09-07T07:59:00Z', finished_at: '2026-09-07T08:00:00Z',
  created_at: '2026-09-07T07:59:00Z', updated_at: '2026-09-07T08:00:00Z',
};

const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body, blob: async () => new Blob() });

describe('ReportsPage', () => {
  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response([report])).mockResolvedValueOnce(response([scan])));
  });
  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('shows immutable report evidence and preview', async () => {
    render(<ConfigProvider><ReportsPage /></ConfigProvider>);
    expect(await screen.findByText('生产环境漏洞扫描')).toBeInTheDocument();
    expect(screen.getByText('openeuler-20260905')).toBeInTheDocument();
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '预览 生产环境漏洞扫描' }));
    expect(await screen.findByText('文件完整性')).toBeInTheDocument();
    expect(screen.getByText(/SHA-256/)).toBeInTheDocument();
  });
});
