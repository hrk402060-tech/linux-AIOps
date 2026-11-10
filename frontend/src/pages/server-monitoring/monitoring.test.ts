import { describe, expect, it } from 'vitest';

import { coreCount, metric, connection, partitions, interfaces, displayState, rate } from './values';
import type { FleetAsset } from '../../types/monitoring';
import { assetFixture } from './testFixtures';

describe('monitoring values', () => {
  it('does not present offline, stale, failed or disabled metrics as valid', () => {
    const online = assetFixture('a', { cpu_percent: 0, cpu_count: 8 });
    expect(metric(online, 'cpu_percent')).toBe(0);
    for (const asset of [
      { ...online, stale: true }, { ...online, lifecycle_status: 'disabled' },
      { ...online, latest_snapshot: { ...online.latest_snapshot!, status: 'offline' } },
      { ...online, latest_snapshot: null },
    ]) expect(metric(asset, 'cpu_percent')).toBeNull();
    expect(connection({ ...online, stale: true })).toBe('stale');
    expect(connection({ ...online, latest_snapshot: null })).toBe('uncollected');
    expect(metric(assetFixture('a', { disk_percent: 0, disk_observed: false }), 'disk_percent')).toBeNull();
  });

  it('requires a real positive integral logical CPU count', () => {
    for (const cpu_count of [undefined, 0, -1, 1.5, NaN, Infinity, '4']) {
      expect(coreCount(assetFixture('a', { cpu_count }))).toBeNull();
    }
    expect(coreCount(assetFixture('a', { cpu_count: 16 }))).toBe(16);
    expect(displayState(assetFixture('a', { load_1m: 1 }), 'load')).toBe('unknown');
  });

  it('handles missing, invalid and legacy partition data honestly', () => {
    const metrics = { disks: [{ mount: '/data', used_percent: 75, inode_percent: 0, inode_observed: false }] };
    expect(partitions(metrics)[0]).toMatchObject({ mount: '/data', total_bytes: null, inode_percent: null });
    expect(partitions({ disks: 'invalid' })).toEqual([]);
    expect(interfaces({ network_interfaces: [{ name: 'enp3s0', rx_bytes_per_second: -1 }] })[0])
      .toMatchObject({ name: 'enp3s0', rx_bytes_per_second: null, tx_bytes_per_second: null });
  });

  it('never accepts invalid percentages or a normal state without a metric', () => {
    for (const cpu_percent of [NaN, Infinity, -3, 102, '40']) {
      const asset: FleetAsset = assetFixture('a', { cpu_percent });
      expect(metric(asset, 'cpu_percent')).toBeNull();
      expect(displayState(asset, 'cpu')).toBe('unknown');
    }
  });

  it('keeps byte units valid for sub-byte network rates', () => {
    expect(rate(0.25)).toBe('0.3 B/s');
    expect(rate(0)).toBe('0 B/s');
    expect(rate(2048)).toBe('2.0 KiB/s');
    expect(rate(null)).toBe('—');
  });
});
