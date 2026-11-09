import { afterEach, expect, it, vi } from 'vitest';
import { getActiveSessions, getActiveSessionsPage } from './auth';

afterEach(() => vi.unstubAllGlobals());

it('requests bounded pages with an encoded user identifier', async () => {
  const page = { items: [], total: 43, offset: 20, limit: 20 };
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(page)));
  vi.stubGlobal('fetch', fetchMock);
  expect(await getActiveSessionsPage('ops/user & 1', 20, 20)).toEqual(page);
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/auth/sessions/page?user_id=ops%2Fuser%20%26%201&offset=20&limit=20',
    expect.objectContaining({ credentials: 'include', signal: expect.any(AbortSignal) }),
  );
});

it('uses a first page of twenty without changing the legacy list helper', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      items: [], total: 0, offset: 0, limit: 20,
    }))).mockResolvedValueOnce(new Response('[]'));
  vi.stubGlobal('fetch', fetchMock);
  await getActiveSessionsPage('user-1');
  expect(fetchMock).toHaveBeenNthCalledWith(1,
    '/api/v1/auth/sessions/page?user_id=user-1&offset=0&limit=20', expect.any(Object));
  expect(await getActiveSessions('user-1')).toEqual([]);
  expect(fetchMock).toHaveBeenNthCalledWith(2,
    '/api/v1/auth/sessions?user_id=user-1', expect.any(Object));
});
