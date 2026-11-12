import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProbesPage } from './ProbesPage';
import { getProbePolicy, getProbes, getProbeResults, runProbe, updateProbePolicy } from '../api/probes';
import { getCmdbTree } from '../api/cmdb';

vi.mock('../api/cmdb', () => ({ getCmdbTree: vi.fn().mockResolvedValue({ businesses: [] }) }));
vi.mock('../api/probes', () => ({
  getProbePolicy: vi.fn().mockResolvedValue({ allowed_cidrs: [] }),
  getProbes: vi.fn(), getProbeResults: vi.fn(), runProbe: vi.fn(), createProbe: vi.fn(), updateProbe: vi.fn(), updateProbePolicy: vi.fn(),
}));
const currentUser = { id: 'u', username: 'admin', display_name: '管理员', role: 'admin' as const, enabled: true, must_change_password: false, created_at: '', last_login_at: null };
const target = { id: 'p', name: 'API 健康', service_id: 's', service_name: '订单服务', asset_id: 'a', asset_name: 'api-01', api_id: null, url: 'http://192.0.2.40/health', method: 'HEAD' as const, expected_status: 200, timeout_seconds: 5, enabled: true, created_at: '', latest_result: null };

describe('ProbesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getProbePolicy).mockResolvedValue({ allowed_cidrs: ['192.0.2.0/24'], configured: true, source: '平台配置' });
    vi.mocked(getCmdbTree).mockResolvedValue({ businesses: [] });
  });
  it('runs only on human action and shows append-only history', async () => {
    vi.mocked(getProbes).mockResolvedValue({ items: [target], total: 1 });
    vi.mocked(runProbe).mockResolvedValue({ id: 'r' } as Awaited<ReturnType<typeof runProbe>>);
    vi.mocked(getProbeResults).mockResolvedValue({ items: [], total: 0 });
    render(<ProbesPage currentUser={currentUser} />);
    expect(await screen.findByText('API 健康')).toBeInTheDocument();
    expect(runProbe).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '检查 API 健康' }));
    expect(runProbe).toHaveBeenCalledWith('p');
    await userEvent.click(screen.getByRole('button', { name: '历史 API 健康' }));
    expect(getProbeResults).toHaveBeenCalledWith('p', 1);
    expect((await screen.findAllByText('检查历史')).some((entry) => entry.classList.contains('ant-drawer-title'))).toBe(true);
  }, 15000);
  it('service_health: opens the precise history even when the target is not on the first list page', async () => {
    vi.mocked(getProbes).mockResolvedValue({ items: [], total: 12 });
    vi.mocked(getProbeResults).mockResolvedValue({ items: [], total: 0 });
    render(<ProbesPage currentUser={currentUser} initialServiceId="service-selected"
      initialProbe={{ id: 'probe-page-2', name: '指定健康检查' }} />);
    await waitFor(() => expect(getProbeResults).toHaveBeenCalledWith('probe-page-2', 1));
    expect(getProbes).toHaveBeenCalledWith('service-selected', 1);
    expect(screen.getByText('指定健康检查')).toBeInTheDocument();
    expect(runProbe).not.toHaveBeenCalled();
  });
  it('keeps auditors read-only and displays the empty allowlist', async () => {
    vi.mocked(getProbePolicy).mockResolvedValue({ allowed_cidrs: [], configured: false, source: '平台配置' });
    vi.mocked(getProbes).mockResolvedValue({ items: [target], total: 1 });
    render(<ProbesPage currentUser={{ ...currentUser, role: 'auditor' }} />);
    expect(await screen.findByText('API 健康')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '检查 API 健康' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '新增目标' })).toBeDisabled();
    expect(screen.getByText(/未配置批准网段/)).toBeInTheDocument();
  });
  it('shows load failures without claiming healthy service', async () => {
    vi.mocked(getProbes).mockRejectedValue(new Error('连接失败'));
    render(<ProbesPage currentUser={currentUser} />);
    expect((await screen.findAllByText('连接失败')).length).toBeGreaterThan(0);
  });
  it('lets administrators configure the approved probe networks in the page', async () => {
    vi.mocked(getProbePolicy).mockResolvedValue({ allowed_cidrs: [], configured: false, source: '环境变量' });
    vi.mocked(getProbes).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(updateProbePolicy).mockResolvedValue({ allowed_cidrs: ['192.0.2.0/24'], configured: true, source: '平台配置' });
    render(<ProbesPage currentUser={currentUser} />);
    await screen.findByText(/未配置批准网段/);
    await userEvent.click(screen.getByRole('button', { name: '配置批准网段' }));
    await userEvent.type(screen.getByRole('textbox', { name: '批准网段' }), '192.0.2.0/24');
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }));
    expect(updateProbePolicy).toHaveBeenCalledWith('192.0.2.0/24');
    await screen.findByText(/批准网段：192.0.2.0\/24/);
  });
  it('does not mistake an unreadable policy for an empty allowlist', async () => {
    vi.mocked(getProbes).mockResolvedValue({ items: [target], total: 1 });
    vi.mocked(getProbePolicy).mockRejectedValue(new Error('配置读取失败'));
    render(<ProbesPage currentUser={currentUser} />);
    await screen.findByText('批准网段状态未知');
    expect(screen.queryByText(/未配置批准网段/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新增目标' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '检查 API 健康' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '配置批准网段' })).not.toBeInTheDocument();
    expect(updateProbePolicy).not.toHaveBeenCalled();
    vi.mocked(getProbePolicy).mockResolvedValue({ allowed_cidrs: ['192.0.2.0/24'], configured: true, source: '平台配置' });
    await userEvent.click(screen.getByRole('button', { name: '重试读取批准网段' }));
    expect(await screen.findByText(/批准网段：192.0.2.0\/24/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '检查 API 健康' })).toBeEnabled();
  });
  it('loads the policy independently when catalog loading fails', async () => {
    vi.mocked(getProbes).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(getCmdbTree).mockRejectedValue(new Error('业务服务暂时不可用'));
    render(<ProbesPage currentUser={currentUser} />);
    expect(await screen.findByText(/批准网段：192.0.2.0\/24/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '修改批准网段' })).toBeEnabled();
  });
  it('revokes create and run controls after clearing the allowlist', async () => {
    vi.mocked(getProbes).mockResolvedValue({ items: [target], total: 1 });
    vi.mocked(updateProbePolicy).mockResolvedValue({ allowed_cidrs: [], configured: false, source: '平台配置' });
    render(<ProbesPage currentUser={currentUser} />);
    await screen.findByText(/批准网段：192.0.2.0\/24/);
    await userEvent.click(screen.getByRole('button', { name: '修改批准网段' }));
    await userEvent.clear(screen.getByRole('textbox', { name: '批准网段' }));
    await userEvent.click(screen.getByRole('button', { name: '保存配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '检查 API 健康' })).toBeDisabled());
    expect(screen.getByRole('button', { name: '新增目标' })).toBeDisabled();
    expect(runProbe).not.toHaveBeenCalled();
  }, 20000);
});
