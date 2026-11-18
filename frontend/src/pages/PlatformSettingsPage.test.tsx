import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { PlatformSettingsPage } from './PlatformSettingsPage';
import { getPlatformSettings, savePlatformSettings, type PlatformSettingsResponse } from '../api/system';

const settings: PlatformSettingsResponse = {
  settings: {
    basic: { platform_name: 'Linux 平台', timezone: 'Asia/Shanghai', display_density: 'comfortable', default_page: 'dashboard' },
    connection: { connect_timeout_seconds: 15, heartbeat_seconds: 60, host_key_policy: 'accept_first_memory', retry_count: 2, collection_interval_seconds: 300, collection_scope: 'enabled_assets' },
    tasks: { max_concurrency: 2, terminal_max_connections: 10, execution_timeout_seconds: 1800, failure_policy: 'continue', terminal_idle_timeout_seconds: 900, terminal_max_duration_seconds: 14400, require_confirmation: true },
    notifications: { enabled: true, default_channel: 'email', quiet_hours_start: '23:00', quiet_hours_end: '07:00', retry_count: 4 },
    authentication: { local_login_enabled: true, ldap_enabled: false, oidc_enabled: false, new_account_policy: 'admin_only' },
    storage: { retention_days: 365, auto_cleanup_enabled: false, backup_enabled: false, backup_path: '/var/lib/linux-ops/backups', verify_backup_after_create: true },
  },
  security: { login_failure_threshold: 5, lock_duration_seconds: 900, session_ttl_seconds: 43200, require_mfa: false, mfa_enforcement: 'off', allow_user_mfa_reset: true, source: '环境变量', updated_by: '环境变量', updated_at: null },
  ai: { enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key_masked: null, updated_at: null },
  revisions: { basic: 'revision-basic', connection: 'revision-connection' },
};

vi.mock('../api/system', () => ({
  getPlatformSettings: vi.fn(), savePlatformSettings: vi.fn(),
}));
vi.mock('../components/AuthPolicyPanel', () => ({ AuthPolicyPanel: () => <div>安全策略面板</div> }));
vi.mock('../components/AISettingsPanel', () => ({ AISettingsPanel: () => <div>AI 配置面板</div> }));
vi.mock('../components/settings/NotificationSettingsPanel', () => ({ NotificationSettingsPanel: () => <div>通知渠道表单</div> }));
vi.mock('../components/settings/IdentitySettingsPanel', () => ({ IdentitySettingsPanel: () => <div>身份源连接表单</div> }));
vi.mock('../components/settings/StorageSettingsPanel', () => ({ StorageSettingsPanel: () => <div>数据任务面板</div> }));

const admin = { id: 'admin', username: 'admin', display_name: null, role: 'admin' as const, enabled: true, must_change_password: false, created_at: '', last_login_at: null };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getPlatformSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(savePlatformSettings).mockImplementation(async (category, payload) => ({
    ...settings, settings: { ...settings.settings, [category]: payload },
  }));
});

const renderPage = (onBack = vi.fn()) => render(<ConfigProvider theme={{ token: { motion: false } }}>
  <PlatformSettingsPage currentUser={admin} onBack={onBack} />
</ConfigProvider>);

it('shows eight categories, category tabs and no cancelled SSH defaults', async () => {
  renderPage();
  await screen.findByDisplayValue('Linux 平台');
  const categories = within(screen.getByRole('navigation', { name: '设置分类' }));
  expect(categories.getAllByRole('button')).toHaveLength(8);
  expect(screen.getByRole('tab', { name: '平台信息' })).toBeInTheDocument();
  fireEvent.click(categories.getByRole('button', { name: '连接与采集' }));
  expect(screen.getByRole('tab', { name: 'SSH 连接' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: '数据采集' })).toBeInTheDocument();
  expect(screen.queryByText(/默认SSH端口|默认连接用户|跳板与代理/)).not.toBeInTheDocument();
});

it('preserves failed form values and supplies the loaded revision on retry', async () => {
  renderPage();
  const name = await screen.findByRole('textbox', { name: '平台名称' });
  fireEvent.change(name, { target: { value: '生产运维平台' } });
  vi.mocked(savePlatformSettings).mockRejectedValueOnce(new Error('配置已被其他管理员更新，请刷新后重试'));
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
  expect(await screen.findByText('配置已被其他管理员更新，请刷新后重试')).toBeInTheDocument();
  expect(name).toHaveValue('生产运维平台');
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
  await waitFor(() => expect(savePlatformSettings).toHaveBeenCalledTimes(2));
  expect(savePlatformSettings).toHaveBeenLastCalledWith('basic', expect.objectContaining({
    platform_name: '生产运维平台', timezone: 'Asia/Shanghai',
  }), 'revision-basic');
}, 30000);

it('does not fetch settings for a non-admin', () => {
  render(<PlatformSettingsPage currentUser={{ ...admin, role: 'operator' }} onBack={vi.fn()} />);
  expect(screen.getByText('仅管理员可以访问系统设置')).toBeInTheDocument();
  expect(getPlatformSettings).not.toHaveBeenCalled();
});

it('can return to the retained workspace', async () => {
  const back = vi.fn();
  renderPage(back);
  await screen.findByDisplayValue('Linux 平台');
  fireEvent.click(screen.getByRole('button', { name: '返回工作台' }));
  expect(back).toHaveBeenCalledOnce();
});

it('preserves fields across sub-tabs and saves the complete category', async () => {
  renderPage();
  await screen.findByDisplayValue('Linux 平台');
  fireEvent.change(screen.getByRole('textbox', { name: '平台名称' }), { target: { value: '正式平台' } });
  fireEvent.click(screen.getByRole('tab', { name: '显示与默认值' }));
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
  await waitFor(() => expect(savePlatformSettings).toHaveBeenCalledWith('basic', expect.objectContaining({
    platform_name: '正式平台', display_density: 'comfortable', default_page: 'dashboard',
  }), 'revision-basic'));
});
it('validates backup paths before submitting and preserves the original saved category', async () => {
  renderPage();
  await screen.findByDisplayValue('Linux 平台');
  fireEvent.click(screen.getByRole('button', { name: '数据与存储' }));
  fireEvent.click(screen.getByRole('tab', { name: '备份与恢复验证' }));
  const path = screen.getByRole('textbox', { name: '备份目录' });
  await waitFor(() => expect(path).toHaveValue('/var/lib/linux-ops/backups'));
  fireEvent.change(path, { target: { value: '/etc/backups' } });
  expect(path).toHaveValue('/etc/backups');
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }));
  expect(await screen.findByText('备份目录必须位于 /var/lib/linux-ops 下', {}, { timeout: 5000 })).toBeInTheDocument();
  expect(savePlatformSettings).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: '备份目录' })).toHaveValue('/etc/backups');
});
