import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { AIProvidersPanel } from './AIProvidersPanel';

const local = { id: 'local-rules', name: '本地研判引擎', kind: 'local', status: 'ready', requires_api_key: false };
const external = { id: 'deepseek', name: 'DeepSeek', kind: 'external', status: 'disabled', requires_api_key: true, configured: true, enabled: false, api_key_masked: '********' };
const response = (body: unknown, status = 200): Response => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
function setup() {
  const fetchMock = vi.fn().mockResolvedValue(response([local, external]));
  vi.stubGlobal('fetch', fetchMock);
  render(<ConfigProvider theme={{ token: { motion: false } }}><AIProvidersPanel /></ConfigProvider>);
  return fetchMock;
}
afterEach(() => vi.unstubAllGlobals());
it('distinguishes saved credentials from a connected external vulnerability engine', async () => {
  const fetchMock = setup();
  const localRow = within((await screen.findByText('本地研判引擎')).closest('tr')!);
  const externalRow = within((await screen.findByText('DeepSeek')).closest('tr')!);
  expect(localRow.getByText('本地就绪')).toBeInTheDocument();
  expect(localRow.queryByRole('button', { name: '配置密钥' })).not.toBeInTheDocument();
  expect(externalRow.getByText('外部推理未接通')).toBeInTheDocument();
  expect(externalRow.getByText('********')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it('moves provider key configuration into the admin panel with exact request and cleared input', async () => {
  const fetchMock = setup();
  fireEvent.click(await screen.findByRole('button', { name: '配置密钥' }));
  const dialog = within(await screen.findByRole('dialog', { name: '配置 DeepSeek 密钥' }));
  expect(dialog.getByRole('button', { name: '加密保存' })).toBeDisabled();
  fireEvent.change(dialog.getByLabelText('漏洞模型 API Key'), { target: { value: '  test-provider-key  ' } });
  fireEvent.click(dialog.getByRole('button', { name: '加密保存' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/providers/config', expect.objectContaining({ method: 'POST', body: JSON.stringify({ provider_id: 'deepseek', api_key: 'test-provider-key', enabled: false }) })));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: '配置密钥' }));
  expect(within(await screen.findByRole('dialog')).getByLabelText('漏洞模型 API Key')).toHaveValue('');
});
it('retains key validation as a local decryption check, not a connection test', async () => {
  const fetchMock = setup();
  await screen.findByText('DeepSeek');
  fetchMock.mockResolvedValueOnce(response({ configured: true, network_called: false }));
  fireEvent.click(screen.getByRole('button', { name: '校验已存密钥' }));
  expect(await screen.findByText('已存密钥可解密，未验证模型连接。')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/providers/config/deepseek/validate', expect.objectContaining({ method: 'POST', body: '{}' }));
});
it('retains explicit provider enable and disable management', async () => {
  const fetchMock = setup();
  await screen.findByText('DeepSeek');
  fetchMock.mockResolvedValueOnce(response({ provider_id: 'deepseek', enabled: true })).mockResolvedValueOnce(response([local, { ...external, enabled: true }]));
  fireEvent.click(screen.getByRole('button', { name: '启用' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '停用' })).toBeEnabled());
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/providers/config/deepseek/enabled', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: true }) }));
});
it('retains paginated configuration audit in the admin panel without loading it eagerly', async () => {
  const fetchMock = setup();
  await screen.findByText('DeepSeek');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(response({ total: 9, items: [{ id: 'audit-1', target: 'DeepSeek', action: 'configured', operator: 'security-admin', detail: '密钥已加密保存', occurred_at: '2026-10-13T00:00:00Z' }] }));
  fireEvent.click(screen.getByRole('button', { name: 'AI 配置审计' }));
  expect(await screen.findByText('security-admin')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/audit-log?source=ai_provider_config&page=1&page_size=8', expect.anything());
  fetchMock.mockResolvedValueOnce(response({ total: 9, items: [] }));
  fireEvent.click(within(screen.getByRole('dialog')).getByTitle('2'));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/audit-log?source=ai_provider_config&page=2&page_size=8', expect.anything()));
  expect(await screen.findByText('暂无配置审计记录')).toBeInTheDocument();
});
it('retains audit failure feedback and explicit retry', async () => {
  const fetchMock = setup();
  await screen.findByText('DeepSeek');
  fetchMock.mockResolvedValueOnce(response({}, 500));
  fireEvent.click(screen.getByRole('button', { name: 'AI 配置审计' }));
  expect(await screen.findByText('审计日志加载失败')).toBeInTheDocument();
  fetchMock.mockResolvedValueOnce(response({ total: 0, items: [] }));
  fireEvent.click(screen.getByRole('button', { name: '重试配置审计' }));
  expect(await screen.findByText('暂无配置审计记录')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '删除审计' })).not.toBeInTheDocument();
});
it('does not allow stale provider writes after a failed refresh', async () => {
  const fetchMock = setup();
  await screen.findByText('DeepSeek');
  fetchMock.mockResolvedValueOnce(response({ detail: '配置加载失败' }, 500));
  fireEvent.click(screen.getByRole('button', { name: '刷新漏洞模型配置' }));
  expect(await screen.findByText('配置加载失败')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '配置密钥' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '启用' })).toBeDisabled();
});
