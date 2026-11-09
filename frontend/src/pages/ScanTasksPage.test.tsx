import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ScanTasksPage } from './ScanTasksPage';

const task = {
  id: 'task-1', name: '生产区综合检查', scan_type: 'comprehensive' as const,
  status: 'queued' as const, progress: 0, read_only: true, asset_ids: ['asset-1'],
  asset_snapshot: [{ id: 'asset-1', name: 'openEuler 核心节点', host: '10.0.0.8',
    ssh_port: 22, os_family: 'openeuler', environment: 'production',
    group_name: '核心业务', credential_configured: true }],
  knowledge_release_id: 'release-1', knowledge_version: '2026.09.03-demo',
  rerun_of_id: null, result_summary: null, findings: [],
  execution_log: [{ status: 'queued' as const, message: '任务已进入只读扫描队列', occurred_at: '2026-09-03T08:00:00Z' }],
  error_message: null, started_at: null, finished_at: null,
  archived: false, archived_at: null, archived_by: null,
  created_at: '2026-09-03T08:00:00Z', updated_at: '2026-09-03T08:00:00Z',
};
const completedServiceTask = {
  ...task, status: 'completed' as const, progress: 100, scan_type: 'service_vulnerability' as const,
  result_summary: {
    assets_total: 1, assets_scanned: 1, assets_failed: 0, critical: 0, high: 1,
    medium: 0, low: 0, manual_confirmation: 0, evidence_policy: 'vendor_advisory_first',
    collection_mode: 'read_only',
    asset_results: [{ asset_id: 'asset-1', asset_name: 'openEuler 核心节点', status: 'scanned',
      service_count: 1, services: [{ protocol: 'tcp', listen: '0.0.0.0:22', process: 'sshd',
        process_name: 'sshd', pid: '123', executable: '/usr/sbin/sshd',
        package_name: 'openssh-server', installed_version: '1.0-1.oe2403',
        banner: 'SSH-2.0-OpenSSH_9.3' }] }],
  },
};
const asset = {
  id: 'asset-1', name: 'openEuler 核心节点', host: '10.0.0.8', ssh_port: 22,
  os_family: 'openeuler' as const, environment: 'production' as const,
  group_name: '核心业务', credential_id: null, credential: null, labels: [],
  status: 'online' as const, latency_ms: 8, check_message: 'SSH 端口可达',
  lifecycle_status: 'enabled' as const, lifecycle_reason: null,
  lifecycle_operator: null, archived_at: null,
  last_checked_at: '2026-09-03T08:00:00Z', created_at: '2026-09-03T08:00:00Z',
  updated_at: '2026-09-03T08:00:00Z',
};

function response(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe('ScanTasksPage', () => {
  beforeEach(() => {
    sessionStorage.setItem('sentinel-session-token', 'test-token');
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response([task]))
      .mockResolvedValueOnce(response([asset])));
  });
  afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

  it('shows task lifecycle, target and immutable knowledge version', async () => {
    render(<ConfigProvider><ScanTasksPage currentUser={{ role: 'admin' }} /></ConfigProvider>);
    expect(await screen.findByText('生产区综合检查')).toBeInTheDocument();
    expect(screen.getByText('openEuler 核心节点')).toBeInTheDocument();
    expect(screen.getByText('2026.09.03-demo')).toBeInTheDocument();
    expect(screen.getAllByText('只读')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '删除 生产区综合检查' })).not.toBeInTheDocument();
  });

  it('opens creation controls for scan type and assets', async () => {
    render(<ConfigProvider><ScanTasksPage currentUser={{ role: 'admin' }} /></ConfigProvider>);
    await screen.findByText('生产区综合检查');
    await userEvent.click(screen.getByRole('button', { name: '新建任务' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('主机漏洞')).toBeInTheDocument();
    expect(within(drawer).getByText('服务漏洞')).toBeInTheDocument();
    expect(within(drawer).getByText('综合扫描')).toBeInTheDocument();
    expect(within(drawer).getByText('目标资产')).toBeInTheDocument();
    expect(within(drawer).getByText('只读、非破坏执行')).toBeInTheDocument();
  });

  it('starts a queued task and shows the scanning state', async () => {
    const scanning = { ...task, status: 'scanning' as const, progress: 10 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([task]))
      .mockResolvedValueOnce(response([asset]))
      .mockResolvedValueOnce(response(scanning));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><ScanTasksPage currentUser={{ role: 'admin' }} /></ConfigProvider>);
    await screen.findByText('生产区综合检查');
    await userEvent.click(screen.getByRole('button', { name: '启动 生产区综合检查' }));
    await waitFor(() => expect(screen.getByText('扫描中')).toBeInTheDocument());
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/scan-tasks/task-1/start');
  });

  it('shows read-only service package evidence in task details', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response([completedServiceTask]))
      .mockResolvedValueOnce(response([asset])));
    render(<ConfigProvider><ScanTasksPage currentUser={{ role: 'admin' }} /></ConfigProvider>);
    await screen.findByText('生产区综合检查');

    await userEvent.click(screen.getByRole('button', { name: '查看 生产区综合检查' }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('0.0.0.0:22')).toBeInTheDocument();
    expect(within(drawer).getByText('openssh-server')).toBeInTheDocument();
    expect(within(drawer).getByText('1.0-1.oe2403')).toBeInTheDocument();
    expect(within(drawer).getByText('SSH-2.0-OpenSSH_9.3')).toBeInTheDocument();
  });

  it('archives a terminal task without deleting scan evidence', async () => {
    const cancelled = { ...task, status: 'cancelled' as const };
    const archived = { ...cancelled, archived: true, archived_at: '2026-09-08T08:00:00Z', archived_by: 'admin' };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([cancelled]))
      .mockResolvedValueOnce(response([asset]))
      .mockResolvedValueOnce(response(archived))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([asset]));
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><ScanTasksPage currentUser={{ role: 'admin' }} /></ConfigProvider>);
    await screen.findByText('生产区综合检查');

    await userEvent.click(screen.getByRole('button', { name: '归档 生产区综合检查' }));

    await waitFor(() => expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/scan-tasks/task-1/archive'));
  });
});
