from datetime import datetime, timedelta, timezone
from math import isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbLifecycle, CmdbService,
    CmdbServiceAsset, ServiceAssetStatus,
)
from src.services.dashboard_queries import (
    get_finding_counts, get_latest_active_findings, get_latest_snapshots,
)
from src.services.observability_evidence import get_alerts, get_probe_evidence, is_stale
from src.services.observability_health import apply_health

HEALTH_STATES = {"healthy", "degraded", "unavailable", "unobserved"}
STATUS_PRIORITY = {"unavailable": 0, "degraded": 1, "unobserved": 2, "healthy": 3}
DEFAULT_MONITORING_INTERVAL_SECONDS = 300
MIN_FRESHNESS_SECONDS = 15 * 60


def freshness_threshold_seconds(monitoring_interval_seconds: int) -> int:
    return max(MIN_FRESHNESS_SECONDS, monitoring_interval_seconds * 3)


def get_services(
    session: Session, query: str = "", business_id: str | None = None,
    health_status: str | None = None,
    visible_asset_ids: set[str] | None = None,
    monitoring_interval_seconds: int = DEFAULT_MONITORING_INTERVAL_SECONDS,
) -> dict[str, object]:
    if health_status and health_status not in HEALTH_STATES:
        raise ValueError("无效的服务健康状态")
    freshness_limit_seconds = freshness_threshold_seconds(monitoring_interval_seconds)
    freshness_threshold = timedelta(seconds=freshness_limit_seconds)
    catalog = _catalog_rows(session, query.strip(), business_id, visible_asset_ids)
    asset_ids = list({relation.asset_id for _, _, _, relation, _ in catalog if relation})
    snapshots = get_latest_snapshots(session, asset_ids)
    alerts = get_alerts(session, asset_ids)
    findings = get_finding_counts(get_latest_active_findings(session, visible_asset_ids))
    now = datetime.now(timezone.utc)
    grouped: dict[str, dict[str, object]] = {}
    for business, application, service, relation, asset in catalog:
        row = grouped.setdefault(service.id, _service_row(business, application, service))
        if relation is not None and asset is not None:
            _append_asset(
                row, asset, snapshots.get(asset.id), alerts, findings,
                now, freshness_threshold,
            )
    evidence = get_probe_evidence(
        session, grouped, now, freshness_threshold, visible_asset_ids is not None,
    )
    services = [
        apply_health(_finalize(row), evidence[key], visible_asset_ids is not None)
        for key, row in grouped.items()
    ]
    if health_status:
        services = [item for item in services if item["status"] == health_status]
    services.sort(key=lambda item: (
        STATUS_PRIORITY[str(item["status"])], -int(item["active_alerts"]),
        str(item["business_name"]), str(item["service_name"]),
    ))
    return {
        "summary": _summary(services), "services": services,
        "monitoring_mode": "ssh_agentless_near_realtime",
        "freshness_threshold_seconds": freshness_limit_seconds,
    }


def _catalog_rows(
    session: Session, query: str, business_id: str | None,
    visible_asset_ids: set[str] | None,
) -> list[tuple[CmdbBusiness, CmdbApplication, CmdbService, CmdbServiceAsset | None, Asset | None]]:
    statement = (
        select(CmdbBusiness, CmdbApplication, CmdbService, CmdbServiceAsset, Asset)
        .join(CmdbApplication, CmdbApplication.business_id == CmdbBusiness.id)
        .join(CmdbService, CmdbService.application_id == CmdbApplication.id)
        .outerjoin(CmdbServiceAsset, (
            (CmdbServiceAsset.service_id == CmdbService.id)
            & (CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE)
        ))
        .outerjoin(Asset, (
            (Asset.id == CmdbServiceAsset.asset_id)
            & (Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED)
        ))
        .where(
            CmdbBusiness.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbApplication.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbService.lifecycle_status == CmdbLifecycle.ACTIVE,
        )
    )
    if business_id:
        statement = statement.where(CmdbBusiness.id == business_id)
    if visible_asset_ids is not None:
        statement = statement.where(Asset.id.in_(visible_asset_ids))
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            CmdbService.name.ilike(pattern) | CmdbService.code.ilike(pattern)
            | CmdbApplication.name.ilike(pattern) | CmdbBusiness.name.ilike(pattern)
        )
    # Never truncate joined rows: a partial asset list can falsely imply health.
    return list(session.execute(statement).all())


def _service_row(
    business: CmdbBusiness, application: CmdbApplication, service: CmdbService,
) -> dict[str, object]:
    return {
        "business_id": business.id, "business_name": business.name,
        "application_id": application.id, "application_name": application.name,
        "service_id": service.id, "service_name": service.name,
        "service_code": service.code, "service_type": service.service_type,
        "assets": [], "active_alerts": 0, "pending_vulnerabilities": 0,
        "critical": 0, "high": 0, "stale_assets": 0, "metric_values": {},
        "collected_times": [], "alerts": [],
    }


def _append_asset(
    row: dict[str, object], asset: Asset, snapshot: object,
    alerts: dict[str, list[dict[str, object]]], findings: dict[str, dict[str, object]],
    now: datetime, freshness_threshold: timedelta,
) -> None:
    status = "disabled" if asset.lifecycle_status == AssetLifecycleStatus.DISABLED else (
        getattr(snapshot, "status", "uncollected") if snapshot else "uncollected"
    )
    freshness = (
        "unobserved"
        if asset.lifecycle_status == AssetLifecycleStatus.DISABLED or snapshot is None
        else "stale"
        if is_stale(snapshot.collected_at, now, freshness_threshold)
        else "fresh"
    )
    assets = row["assets"]
    assert isinstance(assets, list)
    assets.append({
        "asset_id": asset.id, "asset_name": asset.name, "host": asset.host,
        "status": status, "freshness": freshness,
        "collected_at": snapshot.collected_at if snapshot else None,
    })
    if freshness == "stale":
        row["stale_assets"] = int(row["stale_assets"]) + 1
    asset_alerts = alerts.get(asset.id, [])
    row["alerts"].extend(asset_alerts)  # type: ignore[union-attr]
    row["active_alerts"] = int(row["active_alerts"]) + len(asset_alerts)
    risk = findings.get(asset.id, {})
    for field in ("total", "critical", "high"):
        target = "pending_vulnerabilities" if field == "total" else field
        row[target] = int(row[target]) + int(risk.get(field, 0))
    if snapshot is not None:
        row["collected_times"].append(snapshot.collected_at)  # type: ignore[union-attr]
    if snapshot is not None and freshness == "fresh" and status == "online":
        metrics = row["metric_values"]
        for name in ("cpu_percent", "memory_percent", "disk_percent"):
            value = snapshot.metrics.get(name)  # type: ignore[union-attr]
            if isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value) and 0 <= value <= 100:
                metrics.setdefault(name, []).append(float(value))  # type: ignore[union-attr]


def _finalize(row: dict[str, object]) -> dict[str, object]:
    assets = row.pop("assets")
    assert isinstance(assets, list)
    enabled = [item for item in assets if item["status"] != "disabled"]
    statuses = [str(item["status"]) for item in enabled if item["freshness"] == "fresh"]
    freshnesses = [str(item["freshness"]) for item in enabled]
    online = statuses.count("online")
    offline = statuses.count("offline")
    disabled = len(assets) - len(enabled)
    uncollected = sum(item["freshness"] == "unobserved" or (
        item["freshness"] == "fresh" and item["status"] not in {"online", "offline"}
    ) for item in enabled)
    if enabled and offline == len(enabled):
        health = "unavailable"
    elif offline:
        health = "degraded"
    elif not enabled or online < len(enabled):
        health = "unobserved"
    else:
        health = "healthy"
    if not freshnesses or all(item == "unobserved" for item in freshnesses):
        freshness = "unobserved"
    elif any(item == "stale" for item in freshnesses):
        freshness = "stale"
    elif any(item == "unobserved" for item in freshnesses):
        freshness = "partial"
    else:
        freshness = "fresh"
    metrics = row.pop("metric_values")
    times = row.pop("collected_times")
    return {
        **row, "status": health, "freshness": freshness, "asset_count": len(assets),
        "online_assets": online, "offline_assets": offline,
        "uncollected_assets": uncollected, "disabled_assets": disabled,
        "resource_peaks": {
            name: max(metrics.get(name, []), default=None)
            for name in ("cpu_percent", "memory_percent", "disk_percent")
        },
        "last_collected_at": max(times) if times else None, "assets": assets,
    }


def _summary(services: list[dict[str, object]]) -> dict[str, int]:
    return {
        "total": len(services),
        **{status: sum(item["status"] == status for item in services) for status in HEALTH_STATES},
    }
