import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getAlertAnalyses, getAlertTimeline, getExternalAlert, getExternalAlerts,
  type AlertAnalysis, type AlertTimelineEntry, type ExternalAlert,
} from '../../api/externalAlerts';
import { isActiveDiagnosticJob } from './DiagnosticJobTag';

export interface AlertDetail {
  alert: ExternalAlert;
  timeline: AlertTimelineEntry[];
  analyses: AlertAnalysis[];
  analysisPage: number;
}

export function useDiagnosticPolling(active: boolean, refresh: () => Promise<void>) {
  const callback = useRef(refresh);
  callback.current = refresh;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let pending = false;
    const run = async () => {
      if (stopped || pending || document.visibilityState !== 'visible') return;
      pending = true;
      try { await callback.current(); }
      finally { pending = false; }
    };
    const onVisibility = () => { if (document.visibilityState === 'visible') void run(); };
    const timer = window.setInterval(() => void run(), 5000);
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisibility); };
  }, [active]);
}

export function useExternalAlertData(filters: Record<string, string>, page: number, onError: (text: string) => void) {
  const [data, setData] = useState<{
    total: number;
    items: ExternalAlert[];
    summary?: { open: number; urgent: number; pending_rca: number; linked: number };
  }>({ total: 0, items: [] });
  const [detail, setDetail] = useState<AlertDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [authoritative, setAuthoritative] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailAuthoritative, setDetailAuthoritative] = useState(false);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const listPending = useRef<{ key: string; request: Promise<void> } | null>(null);
  const detailPending = useRef<{ key: string; request: Promise<void> } | null>(null);
  const detailRef = useRef(detail);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  detailRef.current = detail;
  useEffect(() => () => {
    listGeneration.current += 1; detailGeneration.current += 1;
    listPending.current = null; detailPending.current = null;
  }, []);
  const load = useCallback((): Promise<void> => {
    const key = JSON.stringify([filters, page]);
    if (listPending.current?.key === key) return listPending.current.request;
    const generation = ++listGeneration.current;
    setLoading(true);
    const request = (async () => {
      try {
        const result = await getExternalAlerts({ ...filters, offset: String((page - 1) * 20), limit: '20' });
        if (generation === listGeneration.current) { setData(result); setAuthoritative(true); }
      } catch (error) {
        if (generation === listGeneration.current) {
          setAuthoritative(false);
          errorRef.current(error instanceof Error ? error.message : '外部告警加载失败');
        }
      } finally {
        if (generation === listGeneration.current) { setLoading(false); listPending.current = null; }
      }
    })();
    listPending.current = { key, request }; return request;
  }, [filters, page]);
  useEffect(() => { setAuthoritative(false); void load(); }, [load]);
  const showDetail = useCallback((item: ExternalAlert, requestedPage?: number): Promise<void> => {
    const analysisPage = requestedPage ?? (detailRef.current?.alert.id === item.id ? detailRef.current.analysisPage : 1);
    const key = `${item.id}:${analysisPage}`;
    if (detailPending.current?.key === key) return detailPending.current.request;
    const generation = ++detailGeneration.current;
    setDetailAuthoritative(false);
    setDetailLoading(true);
    const request = (async () => {
      try {
        const [alert, timeline, analyses] = await Promise.all([
          getExternalAlert(item.id), getAlertTimeline(item.id), getAlertAnalyses(item.id, (analysisPage - 1) * 20, 20),
        ]);
        if (generation === detailGeneration.current) {
          setDetail({ alert, timeline, analyses, analysisPage });
          setDetailAuthoritative(true);
        }
      } catch (error) {
        if (generation === detailGeneration.current) {
          setDetailAuthoritative(false);
          errorRef.current(error instanceof Error ? error.message : '告警详情加载失败');
        }
      } finally {
        if (generation === detailGeneration.current) {
          setDetailLoading(false);
          detailPending.current = null;
        }
      }
    })();
    detailPending.current = { key, request }; return request;
  }, []);
  const closeDetail = useCallback(() => {
    detailGeneration.current += 1;
    detailPending.current = null;
    setDetailLoading(false);
    setDetailAuthoritative(false);
    setDetail(null);
  }, []);
  const refresh = useCallback(async () => {
    const current = detailRef.current;
    if (current) {
      detailGeneration.current += 1;
      detailPending.current = null;
    }
    await Promise.all([load(), current ? showDetail(current.alert) : Promise.resolve()]);
  }, [load, showDetail]);
  const active = data.items.some((item) => isActiveDiagnosticJob(item.latest_job)) || isActiveDiagnosticJob(detail?.alert.latest_job);
  useDiagnosticPolling(active, refresh);
  return {
    data, detail, loading, authoritative, detailLoading, detailAuthoritative,
    load, refresh, showDetail, closeDetail,
  };
}
