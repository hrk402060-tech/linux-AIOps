import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { NotificationSettingsPanel } from './NotificationSettingsPanel';
import { getWebhookChannel, saveWebhookChannel, testSettingsChannel } from '../../api/settingsNotifications';

vi.mock('../../api/settingsNotifications', () => ({
  getWebhookChannel: vi.fn(), saveWebhookChannel: vi.fn(), testSettingsChannel: vi.fn(),
}));
const channel = { enabled: true, url_masked: 'https://hooks.example/****', token_configured: true, updated_at: null };
beforeEach(() => {
  vi.clearAllMocks(); vi.mocked(getWebhookChannel).mockResolvedValue(channel);
  vi.mocked(saveWebhookChannel).mockResolvedValue(channel);
});
const mount = () => render(<ConfigProvider theme={{ token: { motion: false } }}><NotificationSettingsPanel channel="webhook" /></ConfigProvider>);
it('does not expose stored URL/token and preserves failed replacement input', async () => {
  mount();
  const url = await screen.findByLabelText('Webhook 地址');
  expect(url).toHaveValue('');
  expect(screen.getByLabelText('认证密钥')).toHaveValue('');
  fireEvent.change(url, { target: { value: 'https://hooks.example/new' } });
  fireEvent.change(screen.getByLabelText('认证密钥'), { target: { value: 'pending-private-key' } });
  vi.mocked(saveWebhookChannel).mockRejectedValueOnce(new Error('保存冲突'));
  fireEvent.click(screen.getByRole('button', { name: '保存消息渠道' }));
  expect(await screen.findByText('保存冲突')).toBeInTheDocument();
  expect(url).toHaveValue('https://hooks.example/new');
  expect(screen.getByLabelText('认证密钥')).toHaveValue('pending-private-key');
});
it('displays the actual saved-channel test failure', async () => {
  mount();
  await screen.findByText(channel.url_masked);
  vi.mocked(testSettingsChannel).mockResolvedValue({ ok: false, detail: '对端拒绝认证', checked_at: '2026-11-17T00:00:00Z' });
  fireEvent.click(screen.getByRole('button', { name: '测试已保存渠道' }));
  expect(await screen.findByText('对端拒绝认证')).toBeInTheDocument();
  expect(testSettingsChannel).toHaveBeenCalledWith('webhook');
  expect(saveWebhookChannel).not.toHaveBeenCalled();
});
it('rejects insecure webhook addresses before saving', async () => {
  mount(); await screen.findByLabelText('Webhook 地址');
  fireEvent.change(screen.getByLabelText('Webhook 地址'), { target: { value: 'http://hooks.example/private' } });
  fireEvent.click(screen.getByRole('button', { name: '保存消息渠道' }));
  await waitFor(() => expect(screen.getByText('请输入不含用户信息和片段的 HTTPS 地址')).toBeInTheDocument());
  expect(saveWebhookChannel).not.toHaveBeenCalled();
});
