import { afterEach, describe, expect, it, vi } from 'vitest';
import { removeScanTask, ScanTaskRequestError } from './scan-tasks';

afterEach(() => vi.unstubAllGlobals());

describe('removeScanTask', () => {
  it('sends explicit DELETE confirmation and trimmed reason without parsing a 204 body', async () => {
    const json = vi.fn().mockRejectedValue(new Error('204 has no JSON body'));
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204, json });
    vi.stubGlobal('fetch', fetchMock);
    await expect(removeScanTask('task-1', '  任务已完成  ')).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/scan-tasks/task-1', expect.objectContaining({
      method: 'DELETE', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmation: 'DELETE task-1', reason: '任务已完成' }),
      signal: expect.any(AbortSignal),
    }));
    expect(json).not.toHaveBeenCalled();
  });

  it.each([401, 403, 409, 500])('preserves HTTP %s as a typed error and never retries', async (status) => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status, json: async () => ({ detail: '操作被拒绝' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    let error: unknown;
    try { await removeScanTask('task-1', '任务已完成'); }
    catch (caught) { error = caught; }
    expect(error).toBeInstanceOf(ScanTaskRequestError);
    expect(error).toMatchObject({ status, message: '操作被拒绝' });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('preserves HTTP status even when an error body is not JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 502, json: async () => { throw new SyntaxError('Invalid JSON'); },
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(removeScanTask('task-1', '任务已完成')).rejects.toMatchObject({
      status: 502, name: 'ScanTaskRequestError',
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('does not turn an ambiguous network error into an HTTP rejection or retry', async () => {
    const failure = new TypeError('Failed to fetch');
    const fetchMock = vi.fn().mockRejectedValue(failure);
    vi.stubGlobal('fetch', fetchMock);
    await expect(removeScanTask('task-1', '任务已完成')).rejects.toBe(failure);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
