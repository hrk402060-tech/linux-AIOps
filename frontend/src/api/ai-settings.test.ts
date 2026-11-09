import { afterEach, expect, it, vi } from 'vitest';
import { getTerminalAISettings, isValidTerminalAIUrl, saveTerminalAISettings } from './ai-settings';

const settings = { enabled: true, base_url: 'http://192.0.2.7:11434/v1', model: 'local-model', timeout_seconds: 30, api_key_masked: '********', updated_at: '2026-10-13T00:00:00Z' };
afterEach(() => vi.unstubAllGlobals());
it('reads masked terminal configuration using the authenticated endpoint', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => settings });
  vi.stubGlobal('fetch', fetchMock);
  expect(await getTerminalAISettings()).toEqual(settings);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({ credentials: 'include' }));
});
it('saves an empty key as null without implicitly clearing the stored key', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => settings });
  vi.stubGlobal('fetch', fetchMock);
  const payload = { enabled: true, base_url: settings.base_url, model: settings.model, timeout_seconds: 30, api_key: null, clear_api_key: false };
  expect(await saveTerminalAISettings(payload)).toEqual(settings);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agent/settings/terminal', expect.objectContaining({ method: 'PUT', body: JSON.stringify(payload), credentials: 'include' }));
});
it('reports failed reads rather than accepting missing configuration', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '只有管理员可管理 AI 配置' }) }));
  await expect(getTerminalAISettings()).rejects.toThrow('只有管理员可管理 AI 配置');
});
it('reports failed saves without treating them as saved', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '模型配置保存失败' }) }));
  await expect(saveTerminalAISettings({ enabled: false, base_url: '', model: '', timeout_seconds: 30, api_key: null, clear_api_key: false })).rejects.toThrow('模型配置保存失败');
});
it.each(['http://127.0.0.1:11434/v1', 'https://model.example/v1'])('accepts an HTTP model endpoint %s', (value) => {
  expect(isValidTerminalAIUrl(value)).toBe(true);
});
it.each(['', 'ftp://model.example', 'https://user:secret@model.example/v1', 'https://model.example/v1?key=secret', 'https://model.example/v1#secret', 'https://model.example/v1?', 'https://model.example/v1#', 'http://model.example:0/v1', 'http://model.example\\v1', 'http://model.example/v 1'])('rejects an unsafe or invalid endpoint %s', (value) => {
  expect(isValidTerminalAIUrl(value)).toBe(false);
});
