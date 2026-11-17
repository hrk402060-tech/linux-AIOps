import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  getKubernetesClusters, kubernetesRequestError, updateKubernetesCluster, type KubernetesCluster,
} from '../api/kubernetes';
import { getKubernetesOverview, refreshKubernetesOverview, type KubernetesOverview } from '../api/kubernetesOverview';

export function useKubernetesOverview(allowed: boolean, initialCluster?: KubernetesCluster) {
  const [clusters, setClusters] = useState<KubernetesCluster[]>(initialCluster ? [initialCluster] : []);
  const [total, setTotal] = useState(0);
  const [nextOffset, setNextOffset] = useState(0);
  const [selectedId, setSelectedId] = useState(initialCluster?.id ?? '');
  const [data, setData] = useState<KubernetesOverview | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [pollSeconds, setPollSeconds] = useState(30);
  const sequence = useRef(0);
  const listSequence = useRef(0);
  const live = useRef(allowed);
  const selected = useRef(selectedId);
  const pending = useRef(false);
  const readController = useRef<AbortController | null>(null);
  const writeController = useRef<AbortController | null>(null);
  live.current = allowed;
  selected.current = selectedId;
  useEffect(() => {
    live.current = allowed;
    return () => {
      live.current = false; sequence.current += 1; listSequence.current += 1;
      readController.current?.abort(); writeController.current?.abort();
    };
  }, [allowed]);

  const loadClusters = useCallback(async (append = false, offset = 0) => {
    if (!live.current) return;
    const serial = ++listSequence.current;
    setListLoading(true); setListError('');
    try {
      const result = await getKubernetesClusters({ offset, limit: 50, search });
      if (!live.current || serial !== listSequence.current) return;
      setClusters((previous) => {
        const base = append ? previous : search ? [] : previous.filter((item) => item.id === selected.current);
        return [...new Map([...base, ...result.items].map((item) => [item.id, item])).values()];
      });
      setTotal(result.total);
      setNextOffset(offset + result.items.length);
      if (!append && (search || !selected.current)) {
        const next = result.items.some((item) => item.id === selected.current) ? selected.current
          : result.items.find((item) => item.lifecycle === 'active')?.id ?? result.items[0]?.id ?? '';
        if (next !== selected.current) {
          readController.current?.abort(); sequence.current += 1;
          selected.current = next; setData(null); setSelectedId(next);
        }
      }
    } catch (failure) {
      if (live.current && serial === listSequence.current) setListError(kubernetesRequestError(failure));
    } finally {
      if (live.current && serial === listSequence.current) setListLoading(false);
    }
  }, [search]);
  useEffect(() => {
    if (allowed) void loadClusters();
    return () => { listSequence.current += 1; };
  }, [allowed, loadClusters]);

  const load = useCallback(async () => {
    const id = selectedId;
    if (!id || !live.current) return;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    const serial = ++sequence.current;
    setLoading(true);
    try {
      const result = await getKubernetesOverview(id, controller.signal);
      if (!live.current || selected.current !== id || serial !== sequence.current || result.cluster_id !== id) return;
      setData(result); setError('');
    } catch (failure) {
      if (!controller.signal.aborted && live.current && selected.current === id && serial === sequence.current) {
        setError(kubernetesRequestError(failure));
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) setData(null);
      }
    } finally {
      if (readController.current === controller) readController.current = null;
      if (live.current && selected.current === id && serial === sequence.current) setLoading(false);
    }
  }, [selectedId]);
  useEffect(() => {
    if (allowed) void load();
    return () => { sequence.current += 1; readController.current?.abort(); writeController.current?.abort(); };
  }, [allowed, load]);
  const collecting = data?.collection_state === 'queued' || data?.collection_state === 'running';
  useEffect(() => {
    if (!allowed || !selectedId || (!collecting && !pollSeconds)) return;
    const timer = window.setInterval(() => {
      if (!document.hidden && !pending.current) void load();
    }, collecting ? 2_000 : pollSeconds * 1_000);
    return () => window.clearInterval(timer);
  }, [allowed, selectedId, collecting, pollSeconds, load]);

  const refresh = async () => {
    const id = selectedId;
    if (!id || !live.current || pending.current) return;
    pending.current = true; setBusy(true); setError(''); setNotice('');
    const controller = new AbortController();
    writeController.current = controller;
    try {
      const result = await refreshKubernetesOverview(id, controller.signal);
      if (!live.current || selected.current !== id || controller.signal.aborted) return;
      setNotice(result.merged ? '正在采集中，已合并刷新请求' : '已加入采集队列');
      await load();
    } catch (failure) {
      if (live.current && selected.current === id && !controller.signal.aborted) setError(kubernetesRequestError(failure));
    } finally {
      pending.current = false;
      if (live.current) setBusy(false);
    }
  };
  const cluster = clusters.find((item) => item.id === selectedId);
  const setCollectionInterval = async (seconds: 0 | 60 | 300) => {
    if (!cluster || !live.current || pending.current) return;
    const id = cluster.id;
    pending.current = true; setBusy(true); setError('');
    try {
      const updated = await updateKubernetesCluster(id, {
        collection_interval_seconds: seconds, expected_revision: cluster.revision,
      });
      if (!live.current || selected.current !== id) return;
      setClusters((previous) => previous.map((item) => item.id === updated.id ? updated : item));
      await load();
    } catch (failure) {
      if (live.current && selected.current === id) {
        setError(kubernetesRequestError(failure));
        await loadClusters();
      }
    } finally {
      pending.current = false;
      if (live.current) setBusy(false);
    }
  };
  const selectCluster = (id: string) => {
    readController.current?.abort(); writeController.current?.abort();
    sequence.current += 1;
    selected.current = id;
    setData(null); setError(''); setNotice(''); setSelectedId(id);
  };
  return {
    clusters, total, nextOffset, cluster, selectedId, selectCluster, data, loading, listLoading, busy, error, listError, notice,
    collecting, pollSeconds, setPollSeconds, refresh, loadClusters, search, setSearch, setCollectionInterval,
  };
}
