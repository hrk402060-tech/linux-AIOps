import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { KnowledgePage } from './KnowledgePage';

const summary = {
  active_version: '2026.09.03', advisory_count: 1, source_count: 9,
  domestic_source_count: 4, last_updated_at: '2026-09-03T08:00:00Z',
};
const advisory = {
  id: 'adv-1', cve_id: 'CVE-2026-1001', advisory_id: 'openEuler-SA-2026-1001',
  title: '内核权限提升漏洞', severity: 'high', os_family: 'openeuler',
  source_name: 'openEuler Security Advisories',
  vendor_url: 'https://www.openeuler.org/zh/security/security-bulletins/',
  affected_packages: [{ name: 'kernel', affected_version: '< 6.6', fixed_version: '6.6.0-95.oe2403', os_versions: ['24.03-LTS-SP4'] }],
  published_at: '2026-09-01T08:00:00Z', updated_at: '2026-09-03T08:00:00Z',
};
const sources = [
  { id: 'cnvd', name: 'CNVD', provider: 'CNCERT', sync_mode: 'offline_package', priority: 50, enabled: true, online_url: null, schedule: null, last_sync_at: null },
  { id: 'oe', name: 'openEuler Security Advisories', provider: 'openEuler', sync_mode: 'vendor_official_api', priority: 10, enabled: true, online_url: 'https://www.openeuler.org/api-cve/', schedule: '0 2 * * *', last_sync_at: null },
];
const coverage = {
  is_complete: false,
  completeness_note: '当前知识库仅覆盖已成功发布的数据源，不代表所有 Linux 漏洞。',
  publication_policy: 'stage_validate_publish',
  failure_policy: 'retain_active_release_and_alert',
  critical_check_interval_hours: 4,
  sources: [
    { source_id: 'oe', source_name: 'openEuler Security Advisories', provider: 'openEuler', os_families: ['openeuler'], sync_mode: 'vendor_official_api', advisory_count: 1, latest_advisory_at: '2026-09-03T08:00:00Z', last_success_at: '2026-09-03T08:00:00Z', latest_sync_status: 'completed', health: 'healthy', is_stale: false, has_alert: false, stale_after_hours: 48, daily_schedule: '0 2 * * *', critical_check_interval_hours: 4 },
    { source_id: 'cnvd', source_name: 'CNVD', provider: 'CNCERT', os_families: ['other'], sync_mode: 'offline_package', advisory_count: 0, latest_advisory_at: null, last_success_at: null, latest_sync_status: null, health: 'never_synced', is_stale: true, has_alert: true, stale_after_hours: 720, daily_schedule: null, critical_check_interval_hours: null },
  ],
};
const releases = [{
  id: 'release-1', version: '2026.09.03', name: 'openEuler 公告', package_id: 'pkg-1',
  source_name: 'openEuler Security Advisories', signature_key_id: 'vendor-key',
  content_sha256: 'a'.repeat(64), advisory_count: 1, status: 'active',
  created_at: '2026-09-03T08:00:00Z', activated_at: '2026-09-03T08:00:00Z',
}];
const backfills = [{
  id: 'backfill-1', source_id: 'oe', status: 'failed', current_page: 12, total_pages: 846,
  batch_size: 10, delay_seconds: 2, processed_notices: 110, published_advisories: 1350,
  progress: 1, error_message: '官方接口连接失败', last_release_id: 'release-1',
  started_at: '2026-09-07T08:00:00Z', finished_at: '2026-09-07T08:12:00Z',
  created_at: '2026-09-07T08:00:00Z', updated_at: '2026-09-07T08:12:00Z',
}];

function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe('KnowledgePage', () => {
  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(summary))
      .mockResolvedValueOnce(response([advisory]))
      .mockResolvedValueOnce(response(sources))
      .mockResolvedValueOnce(response(releases))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response(coverage))
      .mockResolvedValueOnce(response(backfills)));
  });

  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('shows the active vendor advisory evidence', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);

    await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/);
    await userEvent.click(screen.getByRole('tab', { name: '公告库' }));
    expect(await screen.findByText('CVE-2026-1001')).toBeInTheDocument();
    expect(screen.getByText('openEuler-SA-2026-1001')).toBeInTheDocument();
    expect(screen.getByText(/6\.6\.0-95\.oe2403/)).toBeInTheDocument();
    expect(screen.getByText(/适用：24\.03-LTS-SP4/)).toBeInTheDocument();
    expect(screen.getByText('2026.09.03')).toBeInTheDocument();
  });

  it('enables the official openEuler synchronization action', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);
    await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/);

    await userEvent.click(screen.getByRole('tab', { name: '数据源' }));

    expect(screen.getByText('官方 HTTPS 接口')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同步 openEuler Security Advisories' })).toBeEnabled();
  });

  it('marks CNVD as offline package first', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);
    await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/);

    await userEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect((await screen.findAllByText('CNVD')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('离线签名包').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: '同步 CNVD' })).not.toBeInTheDocument();
  });

  it('discloses incomplete coverage and source freshness', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);

    expect(await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/)).toBeInTheDocument();
    expect(screen.getByText('openEuler Security Advisories')).toBeInTheDocument();
    expect(screen.getByText('从未同步')).toBeInTheDocument();
    expect(screen.getByText('每日 02:00')).toBeInTheDocument();
    expect(screen.getAllByText(/每 4 小时/).length).toBeGreaterThan(0);
  });

  it('opens the signed package import form', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);
    await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/);

    await userEvent.click(screen.getByRole('button', { name: '导入签名包' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('漏洞数据包')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('签名文件')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('可信密钥标识')).toBeInTheDocument();
  });

  it('shows resumable openEuler historical backfill progress', async () => {
    render(<ConfigProvider><KnowledgePage /></ConfigProvider>);
    await screen.findByText(/当前知识库仅覆盖已成功发布的数据源/);

    await userEvent.click(screen.getByRole('tab', { name: '历史回填' }));

    expect(screen.getByText('110 / 8460 条公告')).toBeInTheDocument();
    expect(screen.getByText('1350 条 CVE')).toBeInTheDocument();
    expect(screen.getByText('官方接口连接失败')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '续跑历史回填' })).toBeEnabled();
  });
});
