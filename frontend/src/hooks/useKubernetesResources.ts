import { useEffect, useRef, useState } from 'react';
import {
  getKubernetesResource, getKubernetesResources,
  type KubernetesResourceDetail, type KubernetesResourceItem, type KubernetesResourceKind,
  type KubernetesResourceList,
} from '../api/kubernetesOverview';
import { kubernetesRequestError } from '../api/kubernetes';

export function useKubernetesResources(
  clusterId: string | undefined,
  kind: KubernetesResourceKind,
  filters: { namespace?: string; search?: string; status?: string },
  enabled: boolean,
) {
  const [data, setData] = useState<KubernetesResourceList | null>(null);
  const [detail, setDetail] = useState<KubernetesResourceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const detailRequest = useRef(0);

  useEffect(() => {
    detailController.current?.abort();
    detailController.current = null;
    detailRequest.current += 1;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(false);
    if (!enabled || !clusterId) {
      setData(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void getKubernetesResources(clusterId, kind, filters, controller.signal)
      .then(setData)
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(kubernetesRequestError(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [clusterId, enabled, filters.namespace, filters.search, filters.status, kind]);

  const openDetail = (uid: string) => {
    if (!clusterId) return;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const requestId = ++detailRequest.current;
    setDetailLoading(true);
    setDetail(null);
    setDetailError(null);
    void getKubernetesResource(clusterId, kind, uid, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted && requestId === detailRequest.current) setDetail(value);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted && requestId === detailRequest.current) {
          setDetailError(kubernetesRequestError(failure));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && requestId === detailRequest.current) setDetailLoading(false);
      });
  };

  return {
    data, items: data?.items ?? ([] as KubernetesResourceItem[]), detail, loading, detailLoading,
    error, detailError, openDetail,
    clearDetail: () => {
      detailController.current?.abort();
      detailController.current = null;
      detailRequest.current += 1;
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
    },
  };
}
