import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ExternalAlertRequestError, analyzeExternalAlert, cancelDiagnosticJob, getAlertAnalyses, getDiagnosticJobs,
} from './externalAlerts';

const payload = { confirmation: 'alert-1', reason: '人工排查' };
const job = { id: 'job-1', alert_id: 'alert-1', status: 'queued', display_status: 'queued' };
function stub(body: unknown, status = 202) {
  const fetchMock = vi.fn(async () => ({ ok: status < 400, status, json: async () => body }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe('external alert diagnostic API contract', () => {
  it('returns the accepted enqueue job using the exact alert path and human confirmation body', async () => {
    const fetchMock = stub(job);
    await expect(analyzeExternalAlert('alert-1', payload)).resolves.toEqual(job);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostics', expect.objectContaining({
      method: 'POST', body: JSON.stringify(payload), credentials: 'include', signal: expect.any(AbortSignal),
    }));
  });

  it('cancels the selected job under its alert while preserving exact alert confirmation', async () => {
    const cancelling = { ...job, status: 'running', display_status: 'cancelling' };
    const fetchMock = stub(cancelling);
    await expect(cancelDiagnosticJob('alert-1', 'job-1', payload)).resolves.toEqual(cancelling);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostic-jobs/job-1/cancel', expect.objectContaining({
      method: 'POST', body: JSON.stringify(payload), credentials: 'include',
    }));
  });

  it('exposes a typed conflict for authoritative UI refresh without automatic resubmission', async () => {
    const fetchMock = stub({ detail: '该告警已有活动诊断任务' }, 409);
    try {
      await analyzeExternalAlert('alert-1', payload);
      throw new Error('Expected diagnostic conflict');
    } catch (error) {
      expect(error).toBeInstanceOf(ExternalAlertRequestError);
      expect(error).toMatchObject({ status: 409, message: '该告警已有活动诊断任务' });
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('uses bounded pages for immutable analysis evidence and job history', async () => {
    const fetchMock = stub([], 200);
    await getAlertAnalyses('alert-1', 20, 20);
    await getAlertAnalyses('alert-1', 0, 3, 'job-1');
    await getDiagnosticJobs('alert-1', 10, 10);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/analyses?offset=20&limit=20', expect.any(Object));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/analyses?job_id=job-1&offset=0&limit=3', expect.any(Object));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/alerts/alert-1/diagnostic-jobs?offset=10&limit=10', expect.any(Object));
  });
});
