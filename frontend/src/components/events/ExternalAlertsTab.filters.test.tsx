import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from 'antd';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExternalAlertsTab } from './ExternalAlertsTab';

const asset = {
  id: 'asset-1', name: '演练节点', host: '192.0.2.10', ssh_port: 22,
  os_family: 'openEuler', environment: 'test', group_name: '演练',
  credential_id: null, credential: null, labels: [], status: 'online',
  lifecycle_status: 'enabled', lifecycle_reason: null, lifecycle_operator: null,
  archived_at: null, latency_ms: 8, check_message: null, last_checked_at: null,
  created_at: '', updated_at: '',
};

const alert = {
  id: 'alert-1', connector_id: 'connector-1', connector_name: '演练 Webhook',
  external_id: 'demo-1', title: '演练 CPU 告警', description: '测试告警',
  severity: 'high', status: 'open', asset_id: asset.id, asset_name: asset.name,
  payload: {}, incident_id: null, occurrence_count: 1,
  received_at: '2026-10-20T01:00:00Z', updated_at: '2026-10-20T01:00:00Z',
  latest_analysis: null, latest_job: null, latest_recovery: null,
};

const jsonResponse = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: async () => body } as Response);

describe('ExternalAlertsTab server-side filters', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends search and filter values to the API instead of filtering only the current page', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname === '/api/v1/assets') return jsonResponse([asset]);
      if (url.pathname === '/api/v1/alerts') {
        return jsonResponse({
          total: 1, items: [alert],
          summary: { open: 1, urgent: 1, pending_rca: 1, linked: 0 },
        });
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<ConfigProvider><ExternalAlertsTab currentUser={{ role: 'admin' }} /></ConfigProvider>);

    await screen.findByText('演练 CPU 告警');
    const search = screen.getByRole('searchbox', { name: '搜索外部告警' });
    await user.type(search, 'Disk');
    await user.keyboard('{Enter}');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/alerts?'),
      expect.anything(),
    ));
    const request = fetchMock.mock.calls
      .map(([input]) => new URL(String(input), 'http://localhost'))
      .find((url) => url.pathname === '/api/v1/alerts' && url.searchParams.get('q') === 'Disk');
    expect(request?.searchParams.get('offset')).toBe('0');
    expect(request?.searchParams.get('limit')).toBe('20');
  });
});
