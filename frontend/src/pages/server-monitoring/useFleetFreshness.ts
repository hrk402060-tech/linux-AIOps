import { useEffect, useMemo, useState } from 'react';

import type { FleetOverview } from '../../types/monitoring';

export function useFleetFreshness(fleet: FleetOverview | null): FleetOverview | null {
  const [clock, setClock] = useState(Date.now);
  const receivedAt = useMemo(() => Date.now(), [fleet]);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, []);
  return useMemo(() => {
    if (!fleet) return null;
    const now = Date.parse(fleet.generated_at) + Math.max(0, clock - receivedAt);
    return { ...fleet, assets: fleet.assets.map((asset) => {
      const age = now - Date.parse(asset.latest_snapshot?.collected_at ?? '');
      const stale = asset.stale || !Number.isFinite(age)
        || age < 0 || age > fleet.stale_after_seconds * 1000;
      return { ...asset, stale };
    }) };
  }, [fleet, clock, receivedAt]);
}
