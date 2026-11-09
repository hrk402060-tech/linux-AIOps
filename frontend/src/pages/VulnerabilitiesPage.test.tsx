import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getVulnerabilities, updateVulnerabilityDisposition } from '../api/vulnerabilities';
import { VulnerabilitiesPage } from './VulnerabilitiesPage';

const finding = {
  id: 'finding-1', scan_task_id: 'task-1', scan_task_name: 'gateway assessment',
  asset_id: 'asset-1', asset_name: 'openEuler gateway', knowledge_version: '2026.09.03',
  cve_id: 'CVE-2026-1001', advisory_id: 'openEuler-SA-2026-1001',
  title: 'Kernel privilege boundary issue', severity: 'critical', package_name: 'kernel',
  installed_version: '1.0-1', fixed_version: '1.0-2',
  source_name: 'openEuler Security Advisories', vendor_url: 'https://example.test/advisory',
  conclusion: 'confirmed', confidence: 'high', detected_at: '2026-09-03T06:00:00Z',
  evidence_chain: [
    { type: 'source', value: 'openEuler Security Advisories' },
    { type: 'advisory', value: 'openEuler-SA-2026-1001' },
    { type: 'installed_version', value: '1.0-1' },
    { type: 'fixed_version', value: '1.0-2' },
    { type: 'conclusion', value: 'confirmed' },
  ],
  disposition_status: 'open', disposition_reason: null, disposition_operator: null,
  disposition_changed_at: '2026-09-03T06:00:00Z', risk_accepted_until: null,
  verification_scan_id: null, remediated_version: null, fixed_at: null,
  disposition_history: [{ status: 'open', reason: '扫描首次发现', operator: 'system:scan', occurred_at: '2026-09-03T06:00:00Z', verification_scan_id: null }],
  scan_history: [{ finding_id: 'finding-1', scan_task_id: 'task-1', scan_task_name: 'gateway assessment', detected_at: '2026-09-03T06:00:00Z', knowledge_version: '2026.09.03', installed_version: '1.0-1' }],
};

beforeEach(() => {
  sessionStorage.setItem('sentinel_admin_token', 'test-token');
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve({
    ok: true, status: 200,
    json: async () => url === '/api/v1/assets' ? [{ id: 'asset-1', name: 'openEuler gateway', host: '192.0.2.20' }] : ({
      items: [finding], total: 1,
      summary: { critical: 1, high: 0, medium: 0, low: 0, confirmed: 1, manual_confirmation: 0 },
    }),
  })));
});

describe('VulnerabilitiesPage', () => {
  it('locates a global-search CVE on its asset including resolved history', async () => {
    render(<ConfigProvider><VulnerabilitiesPage initialAssetId="asset-1"
      initialQuery="CVE-2026-1001" initialDisposition="all" /></ConfigProvider>);
    await screen.findAllByText('CVE-2026-1001');
    expect(screen.getByPlaceholderText('搜索 CVE、公告、资产或软件包')).toHaveValue('CVE-2026-1001');
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('query=CVE-2026-1001&asset_id=asset-1&disposition=all'), expect.any(Object),
    ));
  });

  it('shows vulnerability summary and immutable scan evidence', async () => {
    render(<ConfigProvider><VulnerabilitiesPage /></ConfigProvider>);

    expect((await screen.findAllByText('CVE-2026-1001')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('openEuler gateway').length).toBeGreaterThan(0);
    expect(screen.getAllByText('已确认').length).toBeGreaterThan(0);
    expect(screen.getAllByText('2026.09.03').length).toBeGreaterThan(0);
  });

  it('opens the evidence chain in deterministic order', async () => {
    render(<ConfigProvider><VulnerabilitiesPage /></ConfigProvider>);
    await screen.findAllByText('CVE-2026-1001');

    fireEvent.click(screen.getByRole('button', { name: '查看 CVE-2026-1001' }));

    expect(await screen.findByText('证据链')).toBeInTheDocument();
    expect(screen.getByText('数据源')).toBeInTheDocument();
    expect(screen.getByText('已安装版本')).toBeInTheDocument();
    expect(screen.getByText('修复版本')).toBeInTheDocument();
    expect(screen.getByText('厂商公告优先判定，可直接进入修复评估。')).toBeInTheDocument();
  });

  it('keeps medium severity consistent when opening the list evidence', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => ({
      ok: true, status: 200,
      json: async () => String(input) === '/api/v1/assets'
        ? [{ id: 'asset-1', name: 'openEuler gateway', host: '192.0.2.20' }]
        : { items: [{ ...finding, severity: 'medium' }], total: 1,
          summary: { critical: 0, high: 0, medium: 1, low: 0, confirmed: 1, manual_confirmation: 0 } },
    }) as Response);
    render(<ConfigProvider><VulnerabilitiesPage /></ConfigProvider>);
    await screen.findAllByText('CVE-2026-1001');
    const list = screen.getByRole('table');
    expect(within(list).getByText('中危')).toHaveClass('ant-tag-gold');
    fireEvent.click(screen.getByRole('button', { name: '查看 CVE-2026-1001' }));
    const dialog = await screen.findByRole('dialog', { name: '漏洞证据详情' });
    expect(within(dialog).getByText('中危')).toHaveClass('ant-tag-gold');
    expect(within(dialog).queryByText('高危')).not.toBeInTheDocument();
  });

  it('serializes server-side filters and pagination', async () => {
    await getVulnerabilities({
      severity: 'critical', conclusion: 'manual_confirmation', page: 2, pageSize: 20,
    });

    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(
      expect.stringContaining('severity=critical'), expect.any(Object),
    ));
    const url = vi.mocked(fetch).mock.calls.at(-1)?.[0];
    expect(url).toContain('conclusion=manual_confirmation');
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=20');
  });

  it('combines asset and disposition filters', async () => {
    render(<ConfigProvider><VulnerabilitiesPage initialAssetId="asset-1" /></ConfigProvider>);
    await screen.findAllByText('CVE-2026-1001');

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('asset_id=asset-1'), expect.any(Object),
    ));
    expect(screen.getAllByLabelText('资产筛选')[0]).toHaveTextContent('openEuler gateway');
    expect(screen.getAllByLabelText('处置状态筛选')[0]).toHaveTextContent('当前待处理');
  });

  it('records a disposition reason without deleting evidence', async () => {
    const fetchMock = vi.mocked(fetch);
    render(<ConfigProvider><VulnerabilitiesPage /></ConfigProvider>);
    await screen.findAllByText('CVE-2026-1001');

    fireEvent.click(screen.getByRole('button', { name: '处置 CVE-2026-1001' }));
    expect(screen.getAllByLabelText('处置状态').length).toBeGreaterThan(0);
    expect(screen.getByLabelText('处置原因')).toBeInTheDocument();
    expect(screen.queryByLabelText('操作人')).not.toBeInTheDocument();
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ...finding, disposition_status: 'pending_verification', disposition_reason: '补丁已实施' }) } as Response);
    await updateVulnerabilityDisposition('finding-1', {
      status: 'pending_verification', reason: '补丁已实施',
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/vulnerabilities/finding-1/disposition',
      expect.objectContaining({ method: 'PATCH' }),
    );
    expect(screen.queryByRole('button', { name: '删除漏洞' })).not.toBeInTheDocument();
  });
});
