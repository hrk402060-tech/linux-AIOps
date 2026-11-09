import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getAlertAnalyses, getAlertTimeline, getExternalAlert, getExternalAlerts, type ExternalAlert } from '../../api/externalAlerts';
import { useDiagnosticPolling, useExternalAlertData } from './useExternalAlertData';

vi.mock('../../api/externalAlerts', () => ({
  getAlertAnalyses: vi.fn(), getAlertTimeline: vi.fn(), getExternalAlert: vi.fn(), getExternalAlerts: vi.fn(),
}));

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.clearAllMocks(); });

describe('diagnostic queue polling', () => {
  it('polls active jobs only and stops after the job becomes terminal or the tab unmounts', async () => {
    vi.useFakeTimers();
    const poll = vi.fn(async () => undefined);
    const { rerender, unmount } = renderHook(({ active }) => useDiagnosticPolling(active, poll), { initialProps: { active: true } });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(poll).toHaveBeenCalledTimes(1);
    rerender({ active: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(poll).toHaveBeenCalledTimes(1);
    rerender({ active: true });
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('pauses when the document is hidden and refreshes when visibility returns', async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const poll = vi.fn(async () => undefined);
    renderHook(() => useDiagnosticPolling(true, poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(poll).not.toHaveBeenCalled();
    visibility.mockReturnValue('visible');
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('never overlaps refresh while the preceding request is still pending', async () => {
    vi.useFakeTimers();
    let finish: () => void = () => { throw new Error('Request missing'); };
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const poll = vi.fn(() => pending);
    renderHook(() => useDiagnosticPolling(true, poll));
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(poll).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); await pending; });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(poll).toHaveBeenCalledTimes(2);
  });
});

describe('external alert authoritative data', () => {
  it('deduplicates concurrent refreshes for the same visible page', async () => {
    let finish: (value: { total: number; items: ExternalAlert[] }) => void = () => { throw new Error('Request missing'); };
    const pending = new Promise<{ total: number; items: ExternalAlert[] }>((resolve) => { finish = resolve; });
    vi.mocked(getExternalAlerts).mockReturnValue(pending);
    const filters = {};
    const { result } = renderHook(() => useExternalAlertData(filters, 1, vi.fn()));
    act(() => { void result.current.refresh(); void result.current.refresh(); });
    expect(getExternalAlerts).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ total: 0, items: [] }); await pending; });
    expect(result.current.authoritative).toBe(true);
  });

  it('suppresses a late detail response after another alert is selected', async () => {
    vi.mocked(getExternalAlerts).mockResolvedValue({ total: 0, items: [] });
    vi.mocked(getAlertAnalyses).mockResolvedValue([]); vi.mocked(getAlertTimeline).mockResolvedValue([]);
    let finish: (value: ExternalAlert) => void = () => { throw new Error('Request missing'); };
    const pending = new Promise<ExternalAlert>((resolve) => { finish = resolve; });
    const first = { id: 'alert-1', title: '旧告警' } as ExternalAlert;
    const second = { id: 'alert-2', title: '当前告警' } as ExternalAlert;
    vi.mocked(getExternalAlert).mockImplementation((id) => id === first.id ? pending : Promise.resolve(second));
    const filters = {};
    const { result } = renderHook(() => useExternalAlertData(filters, 1, vi.fn()));
    act(() => { void result.current.showDetail(first); void result.current.showDetail(second); });
    await waitFor(() => expect(result.current.detail?.alert.id).toBe(second.id));
    await act(async () => { finish(first); await pending; });
    expect(result.current.detail?.alert.id).toBe(second.id);
  });
});
