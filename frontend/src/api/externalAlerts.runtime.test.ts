import { afterEach, describe, expect, it, vi } from 'vitest';
import { getAlertConnectorReceipts, getAlertConnectorRuntime } from './externalAlerts';

afterEach(() => vi.unstubAllGlobals());

describe('alert connector runtime API contract', () => {
  it('loads the administrator runtime summary with a bounded authenticated request', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => [] }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(getAlertConnectorRuntime()).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alert-connectors/runtime', expect.objectContaining({
      credentials: 'include', signal: expect.any(AbortSignal),
    }));
  });

  it('encodes the connector and omits the result filter for all receipts', async () => {
    const result = { items: [], total: 0, offset: 20, limit: 20 };
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => result }));
    vi.stubGlobal('fetch', fetchMock);
    await getAlertConnectorReceipts('connector/1', 20, 20);
    await getAlertConnectorReceipts('connector/1', 0, 20, 'failure');
    expect(fetchMock).toHaveBeenNthCalledWith(1,
      '/api/v1/alert-connectors/connector%2F1/receipts?offset=20&limit=20', expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2,
      '/api/v1/alert-connectors/connector%2F1/receipts?offset=0&limit=20&result=failure',
      expect.objectContaining({ credentials: 'include', signal: expect.any(AbortSignal) }));
  });
});
