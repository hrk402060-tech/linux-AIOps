import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from 'vitest';
import { AISettingsPanel } from './AISettingsPanel';

vi.mock('./AIProvidersPanel', () => ({ AIProvidersPanel: () => <div>漏洞模型配置区</div> }));
const settings = { enabled: true, base_url: 'http://192.0.2.7:11434/v1', model: 'local-model', timeout_seconds: 30, api_key_masked: '********', updated_at: '2026-10-13T00:00:00Z' };
const response = (body: unknown, status = 200): Response => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
function renderPanel() {
  return render(<ConfigProvider theme={{ token: { motion: false } }}><AISettingsPanel /></ConfigProvider>);
}
function stubSettings() {
  const fetchMock = vi.fn().mockResolvedValue(response(settings));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
afterEach(() => vi.unstubAllGlobals());
it('allows an unconfigured disabled model without supplying a key or endpoint', async () => {
  const empty = { ...settings, enabled: false, base_url: '', model: '', api_key_masked: null, updated_at: null };
  const fetchMock = vi.fn().mockResolvedValue(response(empty));
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();
  await waitFor(() => expect(screen.getByRole('button', { name: '保存运维配置' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key: null, clear_api_key: false }) })));
});
it('shows masked configuration and preserves an existing key when saving empty input', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  expect(screen.getByText('********')).toBeInTheDocument();
  expect(screen.getByLabelText('运维 API Key')).toHaveValue('');
  expect(screen.getByText('漏洞模型配置区')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true, base_url: settings.base_url, model: settings.model, timeout_seconds: 30, api_key: null, clear_api_key: false }) })));
});
it('clears submitted keys after success and prevents duplicate saves while pending', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  let resolve: (value: Response) => void = () => undefined;
  const pending = new Promise<Response>((done) => { resolve = done; });
  fetchMock.mockReturnValueOnce(pending);
  fireEvent.change(screen.getByLabelText('运维 API Key'), { target: { value: '  test-key-123  ' } });
  const save = screen.getByRole('button', { name: '保存运维配置' });
  fireEvent.click(save);
  fireEvent.click(save);
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1));
  expect(save).toBeDisabled();
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ api_key: 'test-key-123', clear_api_key: false });
  await act(async () => resolve(response(settings)));
  await waitFor(() => expect(screen.getByLabelText('运维 API Key')).toHaveValue(''));
});
it('makes explicit key removal mutually exclusive with a replacement key', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  const key = screen.getByLabelText('运维 API Key');
  const clear = screen.getByRole('checkbox', { name: '清除已保存密钥' });
  fireEvent.change(key, { target: { value: 'replacement-key' } });
  expect(clear).toBeDisabled();
  fireEvent.change(key, { target: { value: '' } });
  fireEvent.click(clear);
  expect(key).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ enabled: true, base_url: settings.base_url, model: settings.model, timeout_seconds: 30, api_key: null, clear_api_key: true }) })));
});
it('keeps submission disabled after a failed load and supports an explicit retry', async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(response({ detail: '配置暂时不可用' }, 500)).mockResolvedValue(response(settings));
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();
  expect(await screen.findByText('配置暂时不可用')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存运维配置' })).toBeDisabled();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '重新加载运维配置' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存运维配置' })).toBeEnabled());
});
it.each(['https://user:key@model.example/v1', 'https://model.example/v1?key=secret', 'https://model.example/v1#secret'])('rejects an unsafe model URL without a save %s', async (value) => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  fireEvent.change(screen.getByLabelText('模型接口地址'), { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  expect(await screen.findByText('请输入 HTTP(S) 地址，不得包含用户名、密码、查询参数或片段。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
});
it('validates the timeout range and required enabled model fields', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  fireEvent.change(screen.getByLabelText('请求超时（秒）'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('模型名称'), { target: { value: '   ' } });
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  expect(await screen.findByText('启用时必须填写模型名称。')).toBeInTheDocument();
  expect(await screen.findByText('超时必须为 5 至 120 秒的整数。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
});
it('keeps a failed save visible and does not clear the unsaved key', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  fetchMock.mockResolvedValueOnce(response({ detail: '配置保存失败' }, 500));
  fireEvent.change(screen.getByLabelText('运维 API Key'), { target: { value: 'unsaved-key' } });
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  const panel = within(screen.getByRole('region', { name: '运维 AI 配置' }));
  expect(await panel.findByText('配置保存失败')).toBeInTheDocument();
  expect(screen.getByLabelText('运维 API Key')).toHaveValue('unsaved-key');
  expect(screen.getByRole('button', { name: '保存运维配置' })).toBeEnabled();
});

it('requires replacement or explicit removal before sending an old key to a changed endpoint', async () => {
  const fetchMock = stubSettings();
  renderPanel();
  await waitFor(() => expect(screen.getByLabelText('模型名称')).toHaveValue('local-model'));
  fireEvent.change(screen.getByLabelText('模型接口地址'), { target: { value: 'https://new-model.example/v1' } });
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  expect(await screen.findByText('更换模型地址时请重新填写密钥或选择清除已保存密钥。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0);
  fireEvent.click(screen.getByRole('checkbox', { name: '清除已保存密钥' }));
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1));
});

it('saves a disabled unconfigured model using empty strings, not null endpoint fields', async () => {
  const fetchMock = vi.fn().mockResolvedValue(response({ ...settings, enabled: false, base_url: '', model: '', api_key_masked: null }));
  vi.stubGlobal('fetch', fetchMock); renderPanel();
  await waitFor(() => expect(screen.getByRole('button', { name: '保存运维配置' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '保存运维配置' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({
    method: 'PUT', body: JSON.stringify({ enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key: null, clear_api_key: false }),
  })));
});
