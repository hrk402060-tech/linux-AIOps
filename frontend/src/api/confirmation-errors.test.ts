import { afterEach, describe, expect, it, vi } from 'vitest';
import { getAssets, cleanupAsset } from './assets';
import { approvePatchAdvice } from './patches';
import { removeScanTask } from './scan-tasks';

afterEach(() => vi.unstubAllGlobals());
describe('confirmation API errors', () => {
  it.each([
    () => getAssets(), () => approvePatchAdvice('finding-1', '人工确认', 'APPROVE finding-1'),
    () => removeScanTask('task-1', '任务完成'),
  ])('explains a login failure in Chinese without retrying', async (request) => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401,
      json: async () => ({ detail: 'Authentication required' }) });
    vi.stubGlobal('fetch', fetchMock);
    await expect(request()).rejects.toThrow('登录已失效，请重新登录');
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it('explains structured cleanup validation instead of object Object', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 422,
      json: async () => ({ detail: [{ loc: ['body', 'reason'], type: 'string_too_short',
        msg: 'String should have at least 5 characters', ctx: { min_length: 5 } }] }) });
    vi.stubGlobal('fetch', fetchMock);
    await expect(cleanupAsset('asset-1', '1', 'DELETE asset-1')).rejects.toThrow('操作原因：至少需要 5 个字符');
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it('keeps cleanup and approval machine confirmations unchanged', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    await cleanupAsset('asset-1', '清理测试数据', 'DELETE asset-1');
    await approvePatchAdvice('finding-1', '人工确认', 'APPROVE finding-1');
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/assets/asset-1/cleanup', expect.objectContaining({
      body: JSON.stringify({ reason: '清理测试数据', confirmation: 'DELETE asset-1' }),
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/patches/finding-1/approve', expect.objectContaining({
      body: JSON.stringify({ reason: '人工确认', confirmation: 'APPROVE finding-1' }),
    }));
  });
});
