import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { assetFixture, fleetFixture, trendsFixture } from './testFixtures';
import * as api from '../../api/monitoring';
import { metric } from './values';
import { useFleetFreshness } from './useFleetFreshness';
import { useMonitoringAction } from './useMonitoringAction';
import { TrendChart } from './TrendChart';
import { GlobalTrends } from './GlobalTrends';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('monitoring review regressions', () => {
  it('expires snapshots using server time and elapsed local time without another fetch', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2040-01-01T00:00:00Z'));
    const fleet = fleetFixture();
    fleet.generated_at = '2026-11-10T12:00:00Z';
    fleet.stale_after_seconds = 900;
    fleet.assets = [assetFixture('a', { cpu_percent: 30 })];
    fleet.assets[0].latest_snapshot!.collected_at = '2026-11-10T11:45:05Z';
    const { result } = renderHook(() => useFleetFreshness(fleet));
    expect(metric(result.current!.assets[0], 'cpu_percent')).toBe(30);
    act(() => vi.advanceTimersByTime(10_000));
    expect(result.current!.assets[0].stale).toBe(true);
    expect(metric(result.current!.assets[0], 'cpu_percent')).toBeNull();
  });

  it('prevents duplicate actions and ignores completion after unmount', async () => {
    let resolve: ((value: number) => void) | undefined;
    const operation = vi.fn(() => new Promise<number>((done) => { resolve = done; }));
    const saved = vi.fn();
    const { result, unmount } = renderHook(() => useMonitoringAction());
    act(() => {
      void result.current.run(operation, saved);
      void result.current.run(operation, saved);
    });
    expect(operation).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => { resolve!(1); });
    expect(saved).not.toHaveBeenCalled();
  });

  it('reloads global aggregation when a snapshot expires without changing asset IDs', async () => {
    const getTrends = vi.spyOn(api, 'getFleetTrends').mockResolvedValue(trendsFixture);
    const asset = assetFixture('a', { cpu_percent: 30 });
    const { rerender } = render(<GlobalTrends assets={[asset]} hours={24} revision={0} />);
    await waitFor(() => expect(getTrends).toHaveBeenCalledTimes(1));
    const oldSignal = getTrends.mock.calls[0][2];
    rerender(<GlobalTrends assets={[{ ...asset, stale: true }]} hours={24} revision={0} />);
    await waitFor(() => expect(getTrends).toHaveBeenCalledTimes(2));
    expect(oldSignal?.aborted).toBe(true);
  });

  it('renders invalid numbers as missing and preserves breaks and chronological order', () => {
    const { container } = render(<TrendChart title="CPU" unit="%" maximum={100}
      series={[{ key: 'cpu', label: 'CPU', color: '#0d9488' }]}
      points={[
        { time: '2026-11-10T12:03:00Z', values: { cpu: 30 } },
        { time: '2026-11-10T12:00:00Z', values: { cpu: 10 } },
        { time: '2026-11-10T12:01:00Z', values: { cpu: NaN } },
        { time: '2026-11-10T12:02:00Z', values: { cpu: Infinity } },
      ]} />);
    const path = container.querySelector('path[stroke="#0d9488"]')!;
    expect(path.getAttribute('d')?.match(/M/g)).toHaveLength(2);
    expect(path.getAttribute('d')).not.toMatch(/NaN|Infinity/);
    expect(container.querySelectorAll('circle')).toHaveLength(2);
  });

  it('uses the actual SVG content width for hover and keeps threshold labels outside the plot', () => {
    const { container } = render(<TrendChart title="CPU hover" unit="%" maximum={100}
      series={[{ key: 'cpu', label: 'CPU', color: '#0d9488' }]}
      thresholds={[{ value: 70, label: 'CPU 关注', color: '#b77909' }]}
      points={[{ time: '2026-11-10T12:00:00Z', values: { cpu: 10 } },
        { time: '2026-11-10T13:00:00Z', values: { cpu: 90 } }]} />);
    const svg = screen.getByRole('img', { name: 'CPU hover' });
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, width: 1440, height: 220, right: 1440, bottom: 220,
      x: 0, y: 0, toJSON: () => ({}),
    });
    fireEvent.mouseMove(container.querySelector('rect')!, { clientX: 756 });
    expect(screen.getByRole('status')).toHaveTextContent('90.00 %');
    expect(screen.getByText('CPU 关注 70.0')).toBeInTheDocument();
    expect(svg.querySelector('text[fill="#b77909"]')).toBeNull();
  });
});
