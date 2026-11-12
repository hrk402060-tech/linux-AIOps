from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.cmdb import CmdbLifecycle, CmdbServiceApi
from src.models.monitoring import MonitoringAlertState
from src.models.probes import HttpProbe, HttpProbeRun


def is_stale(observed_at: datetime, now: datetime, threshold: timedelta) -> bool:
    observed_at = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at
    return now - observed_at > threshold or observed_at - now > timedelta(seconds=60)


def get_alerts(session: Session, asset_ids: list[str]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    if not asset_ids:
        return grouped
    alerts = session.scalars(select(Alert).where(
        Alert.asset_id.in_(asset_ids), Alert.status.notin_(("resolved", "closed")),
    ).order_by(Alert.updated_at.desc(), Alert.id.desc()))
    bridged: set[str] = set()
    for alert in alerts:
        internal = alert.source_type == "platform_monitoring"
        if internal and alert.source_key in bridged:
            continue
        if internal and alert.source_key:
            bridged.add(alert.source_key)
        grouped.setdefault(alert.asset_id, []).append({
            "alert_id": alert.id, "asset_id": alert.asset_id,
            "source": "monitoring" if internal else "external",
            "severity": alert.severity, "status": alert.status,
            "message": "关联资产的监控告警" if internal else "关联资产的外部告警",
            "updated_at": alert.updated_at,
        })
    states = session.scalars(select(MonitoringAlertState).where(
        MonitoringAlertState.asset_id.in_(asset_ids), MonitoringAlertState.status == "triggered",
    ).order_by(MonitoringAlertState.asset_id, MonitoringAlertState.metric))
    for state in states:
        if f"{state.asset_id}:{state.metric}" not in bridged:
            grouped.setdefault(state.asset_id, []).append({
                "alert_id": None, "asset_id": state.asset_id, "source": "monitoring",
                "severity": "critical" if state.metric == "availability" else "high",
                "status": "triggered", "message": "关联资产的监控告警",
                "updated_at": state.triggered_at,
            })
    return grouped


def get_probe_evidence(
    session: Session, rows: dict[str, dict[str, object]], now: datetime,
    threshold: timedelta, scoped: bool,
) -> dict[str, dict[str, object]]:
    if not rows:
        return {}
    probes = list(session.scalars(select(HttpProbe).where(
        HttpProbe.service_id.in_(rows),
    ).order_by(HttpProbe.name, HttpProbe.id)))
    latest = _latest_runs(session, [probe.id for probe in probes])
    apis = list(session.scalars(select(CmdbServiceApi).where(
        CmdbServiceApi.service_id.in_(rows), CmdbServiceApi.lifecycle_status == CmdbLifecycle.ACTIVE,
    )))
    api_map = {api.id: api for api in apis}
    result: dict[str, dict[str, object]] = {key: {"probes": [], "api_count": 0} for key in rows}
    for probe in probes:
        row = rows[probe.service_id]
        assets = {item["asset_id"]: item for item in row["assets"]}  # type: ignore[union-attr]
        if probe.asset_id not in assets:
            continue
        if probe.api_id is not None and probe.api_id not in api_map:
            continue
        item = _probe_row(probe, latest.get(probe.id), row, api_map, now, threshold)
        if assets[probe.asset_id]["status"] == "disabled":
            item.update(status="disabled", reason_code="asset_disabled",
                        checked_at=None, latency_ms=None, http_status=None)
        result[probe.service_id]["probes"].append(item)  # type: ignore[union-attr]
    for service_id, row in result.items():
        # A restricted user must not learn hidden API coverage or asset counts.
        row["api_count"] = (
            len({item["api_id"] for item in row["probes"] if item["api_id"]})  # type: ignore[union-attr]
            if scoped else sum(api.service_id == service_id for api in apis)
        )
    return result


def _latest_runs(session: Session, probe_ids: list[str]) -> dict[str, HttpProbeRun]:
    if not probe_ids:
        return {}
    ranked = select(
        HttpProbeRun.id.label("id"),
        func.row_number().over(
            partition_by=HttpProbeRun.probe_id,
            order_by=(HttpProbeRun.checked_at.desc(), HttpProbeRun.id.desc()),
        ).label("position"),
    ).where(HttpProbeRun.probe_id.in_(probe_ids)).subquery()
    runs = session.scalars(select(HttpProbeRun).join(
        ranked, ranked.c.id == HttpProbeRun.id,
    ).where(ranked.c.position == 1))
    return {run.probe_id: run for run in runs}


def _probe_row(
    probe: HttpProbe, run: HttpProbeRun | None, row: dict[str, object],
    apis: dict[str, CmdbServiceApi], now: datetime, threshold: timedelta,
) -> dict[str, object]:
    item: dict[str, object] = {
        "probe_id": probe.id, "probe_name": probe.name, "asset_id": probe.asset_id,
        "api_id": probe.api_id, "status": "unobserved", "reason_code": "never_run",
        "checked_at": None, "latency_ms": None, "http_status": None,
    }
    if probe.status != "active":
        return {**item, "status": "disabled", "reason_code": "probe_disabled"}
    if run is None:
        return item
    context = {"business_id": row["business_id"], "application_id": row["application_id"],
               "service_id": probe.service_id, "asset_id": probe.asset_id, "api_id": probe.api_id}
    if any(getattr(run, key) != value for key, value in context.items()):
        return {**item, "reason_code": "context_changed"}
    if any(run.config_snapshot.get(key) != getattr(probe, key) for key in (
        "url", "method", "expected_status", "timeout_seconds",
    )):
        return {**item, "reason_code": "config_changed"}
    api = apis.get(probe.api_id or "")
    if api and (api.service_id != probe.service_id or api.http_method != probe.method
                or api.path != (urlsplit(probe.url).path or "/")):
        return {**item, "reason_code": "api_changed"}
    item.update(checked_at=run.checked_at, http_status=run.http_status, latency_ms=run.latency_ms)
    if is_stale(run.checked_at, now, threshold):
        return {**item, "status": "stale", "reason_code": "evidence_expired"}
    if run.status not in {"healthy", "unhealthy"}:
        return {**item, "reason_code": "invalid_result", "latency_ms": None, "http_status": None}
    return {**item, "status": "healthy" if run.status == "healthy" else "failed",
            "reason_code": None if run.status == "healthy" else _safe_error(run.error_code)}


def _safe_error(code: str | None) -> str:
    return code if code in {
        "timeout", "connection_error", "tls_error", "http_status_mismatch", "http_error",
    } else "probe_failed"
