from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceAsset,
    CmdbLifecycle, ServiceAssetStatus,
)
from src.models.knowledge import KnowledgeRelease, KnowledgeSyncRun
from src.models.monitoring import MonitoringAlertState, MonitoringPolicy, MonitoringSnapshot
from src.models.scan_task import ScanStatus, ScanTask
from src.services import dashboard_platform
from src.services import knowledge_coverage
from src.services.dashboard_access import DashboardScope, resolve_scope
from src.services.scan_task_lifecycle import not_removed
from src.services.dashboard_queries import (
    get_finding_counts, get_latest_active_findings, get_latest_snapshots,
)
from src.services.observability import freshness_threshold_seconds

KNOWLEDGE_STALE_AFTER = timedelta(hours=24)


def get_snapshot(
    session: Session, visible_asset_ids: set[str] | None = None,
    monitoring_interval_seconds: int = 300,
) -> dict[str, object]:
    scope = resolve_scope(session, visible_asset_ids)
    monitoring_assets = _monitoring_assets(
        session, scope, monitoring_interval_seconds,
    )
    return {
        "assets": _asset_summary(session, scope), "vulnerabilities": _vulnerability_summary(session, scope),
        "scans": _scan_summary(session, scope), "knowledge": _knowledge_summary(session),
        "recent_scans": _recent_scans(session, scope), "affected_assets": _affected_assets(session, scope),
        "resource_alerts": _resource_alert_summary(monitoring_assets),
        "monitoring_assets": monitoring_assets, "resource_trends": _resource_trends(session, scope),
        "active_alerts": _active_alerts(session, scope), "monitoring_mode": "ssh_agentless_near_realtime",
        **dashboard_platform.get_platform_snapshot(
            session, scope, monitoring_interval_seconds,
        ),
    }


def _asset_statement(scope: DashboardScope) -> Select[tuple[Asset]]:
    statement = select(Asset).where(Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED)
    return statement if scope.asset_ids is None else statement.where(Asset.id.in_(scope.asset_ids))


def _asset_summary(session: Session, scope: DashboardScope) -> dict[str, int]:
    assets = list(session.scalars(_asset_statement(scope)))
    enabled = [item for item in assets if item.lifecycle_status == AssetLifecycleStatus.ENABLED]
    latest = get_latest_snapshots(session, [item.id for item in enabled])
    return {
        "total": len(assets),
        "online": sum(latest.get(item.id) is not None and latest[item.id].status == "online" for item in enabled),
        "offline": sum(latest.get(item.id) is not None and latest[item.id].status == "offline" for item in enabled),
        "unknown": sum(item.id not in latest for item in enabled),
        "disabled": sum(item.lifecycle_status == AssetLifecycleStatus.DISABLED for item in assets),
    }


def _vulnerability_summary(session: Session, scope: DashboardScope) -> dict[str, int]:
    findings = get_latest_active_findings(session, scope.asset_ids)
    severity = {level: sum(item.severity == level for item in findings) for level in ("critical", "high", "medium", "low")}
    conclusions = {kind: sum(item.conclusion == kind for item in findings) for kind in ("confirmed", "manual_confirmation")}
    return {
        "total": sum(int(value) for value in severity.values()), "critical": int(severity.get("critical", 0)),
        "high": int(severity.get("high", 0)), "medium": int(severity.get("medium", 0)), "low": int(severity.get("low", 0)),
        "confirmed": int(conclusions.get("confirmed", 0)), "manual_confirmation": int(conclusions.get("manual_confirmation", 0)),
        "pending": len(findings),
    }


def _scan_summary(session: Session, scope: DashboardScope) -> dict[str, int]:
    counts = dict(session.execute(scope.tasks(ScanTask).with_only_columns(ScanTask.status, func.count(ScanTask.id)).where(ScanTask.archived.is_(False), not_removed()).group_by(ScanTask.status)).all())
    queued = int(counts.get(ScanStatus.QUEUED, 0))
    completed, failed = int(counts.get(ScanStatus.COMPLETED, 0)), int(counts.get(ScanStatus.FAILED, 0))
    terminal = completed + failed
    running = sum(int(counts.get(status, 0)) for status in (ScanStatus.SCANNING, ScanStatus.AGGREGATING))
    return {
        "total": sum(int(value) for value in counts.values()), "queued": queued,
        "running": running, "completed": completed, "failed": failed,
        "success_rate": round(completed * 100 / terminal) if terminal else 0,
    }


def _knowledge_summary(session: Session) -> dict[str, object]:
    active = session.scalar(select(KnowledgeRelease).where(KnowledgeRelease.status == "active").order_by(KnowledgeRelease.activated_at.desc()).limit(1))
    latest = session.scalar(select(KnowledgeSyncRun).order_by(KnowledgeSyncRun.started_at.desc()).limit(1))
    coverage = knowledge_coverage.get_coverage(session)
    health_counts = {
        health: sum(item.health == health for item in coverage.sources)
        for health in ("healthy", "stale", "failed", "never_synced")
    }
    successful_times = [
        item.last_success_at for item in coverage.sources if item.last_success_at is not None
    ]
    synced_at = max(successful_times, default=None)
    freshness = _knowledge_freshness(health_counts, len(coverage.sources))
    return {
        "active_version": active.version if active else None,
        "active_advisory_count": active.advisory_count if active else 0,
        "last_sync_status": latest.status if latest else None,
        "last_sync_at": synced_at,
        "freshness": freshness,
        "stale": freshness != "正常",
        "source_count": len(coverage.sources),
        "healthy_source_count": health_counts["healthy"],
        "stale_source_count": health_counts["stale"],
        "failed_source_count": health_counts["failed"],
        "never_synced_source_count": health_counts["never_synced"],
    }


def _knowledge_freshness(counts: dict[str, int], total: int) -> str:
    if total == 0:
        return "未配置"
    if counts["healthy"] == total:
        return "正常"
    if counts["failed"]:
        return "同步失败"
    if counts["never_synced"] == total:
        return "未同步"
    if counts["never_synced"]:
        return "未完整同步"
    return "数据陈旧"


def _recent_scans(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    tasks = session.scalars(scope.tasks(ScanTask).where(ScanTask.archived.is_(False), not_removed()).order_by(ScanTask.created_at.desc()).limit(5))
    return [{
        "id": task.id, "name": task.name, "scan_type": task.scan_type.value,
        "status": task.status.value, "progress": task.progress,
        "finding_count": _finding_count(task), "created_at": task.created_at,
    } for task in tasks]


def _finding_count(task: ScanTask) -> int:
    summary = task.result_summary if isinstance(task.result_summary, dict) else {}
    count = summary.get("total_findings")
    if isinstance(count, int | float) and count >= 0:
        return int(count)
    return len(task.findings or [])


def _affected_assets(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    grouped = get_finding_counts(get_latest_active_findings(session, scope.asset_ids))
    return sorted(grouped.values(), key=lambda item: int(item["total"]), reverse=True)[:5]


def _monitoring_assets(
    session: Session, scope: DashboardScope, monitoring_interval_seconds: int,
) -> list[dict[str, object]]:
    assets = list(session.scalars(_asset_statement(scope).order_by(Asset.name, Asset.id).limit(100)))
    latest = get_latest_snapshots(session, [item.id for item in assets])
    policies = {item.asset_id: item for item in session.scalars(select(MonitoringPolicy).where(MonitoringPolicy.asset_id.in_([item.id for item in assets])))}
    enabled_ids = [item.id for item in assets if item.lifecycle_status == AssetLifecycleStatus.ENABLED]
    alerts = dict(session.execute(
        select(MonitoringAlertState.asset_id, func.count(MonitoringAlertState.id))
        .where(
            MonitoringAlertState.status == "triggered",
            MonitoringAlertState.asset_id.in_(enabled_ids),
        )
        .group_by(MonitoringAlertState.asset_id)
    ).all())
    finding_counts = get_finding_counts(get_latest_active_findings(session, scope.asset_ids))
    service_context = _service_context(session, [item.id for item in assets])
    scans = list(session.scalars(scope.tasks(ScanTask).where(ScanTask.archived.is_(False), not_removed()).order_by(ScanTask.created_at.desc()).limit(100)))
    now = datetime.now(timezone.utc)
    freshness_after = timedelta(
        seconds=freshness_threshold_seconds(monitoring_interval_seconds)
    )
    rows = []
    for asset in assets:
        snapshot, policy = latest.get(asset.id), policies.get(asset.id)
        lifecycle = asset.lifecycle_status.value
        enabled = asset.lifecycle_status == AssetLifecycleStatus.ENABLED
        metrics = dict(snapshot.metrics) if snapshot and enabled else {}
        status = snapshot.status if snapshot and enabled else "disabled" if not enabled else "uncollected"
        vulnerabilities = finding_counts.get(asset.id, {"critical": 0, "high": 0, "total": 0})
        rows.append({"asset_id": asset.id, "asset_name": asset.name, "host": asset.host, "lifecycle_status": lifecycle, "connection_status": status, "metrics": metrics, "collected_at": snapshot.collected_at if snapshot and enabled else None, "stale": bool(snapshot and enabled and now - _aware(snapshot.collected_at) > freshness_after), "active_alerts": int(alerts.get(asset.id, 0)), "critical": int(vulnerabilities["critical"]), "high": int(vulnerabilities["high"]), "pending_vulnerabilities": int(vulnerabilities["total"]), "recent_scan_at": next((item.created_at for item in scans if asset.id in item.asset_ids), None), "cmdb_services": service_context.get(asset.id, []), "thresholds": {"cpu": policy.cpu_threshold if policy else 85, "memory": policy.memory_threshold if policy else 85, "disk": policy.disk_threshold if policy else 85}})
    rows.sort(key=lambda item: (int(item["active_alerts"]), int(item["critical"]), int(item["high"])), reverse=True)
    return rows


def _service_context(session: Session, asset_ids: list[str]) -> dict[str, list[dict[str, str]]]:
    if not asset_ids:
        return {}
    rows = session.execute(select(CmdbServiceAsset, CmdbService, CmdbApplication, CmdbBusiness).join(CmdbService, CmdbService.id == CmdbServiceAsset.service_id).join(CmdbApplication, CmdbApplication.id == CmdbService.application_id).join(CmdbBusiness, CmdbBusiness.id == CmdbApplication.business_id).where(CmdbServiceAsset.asset_id.in_(asset_ids), CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE, CmdbService.lifecycle_status == CmdbLifecycle.ACTIVE, CmdbApplication.lifecycle_status == CmdbLifecycle.ACTIVE, CmdbBusiness.lifecycle_status == CmdbLifecycle.ACTIVE).order_by(CmdbBusiness.name, CmdbApplication.name, CmdbService.name)).all()
    grouped: dict[str, list[dict[str, str]]] = {}
    for relation, service, application, business in rows:
        grouped.setdefault(relation.asset_id, []).append({"business_id": business.id, "business_name": business.name, "application_id": application.id, "application_name": application.name, "service_id": service.id, "service_name": service.name, "service_type": service.service_type, "relation_role": relation.relation_role})
    return grouped


def _resource_alert_summary(rows: list[dict[str, object]]) -> dict[str, int]:
    result = {"cpu": 0, "memory": 0, "disk": 0}
    for row in rows:
        metrics, thresholds = row["metrics"], row["thresholds"]
        if isinstance(metrics, dict) and isinstance(thresholds, dict):
            for name, metric in (("cpu", "cpu_percent"), ("memory", "memory_percent"), ("disk", "disk_percent")):
                result[name] += int(float(metrics.get(metric, 0)) > float(thresholds[name]))
    return result


def _resource_trends(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    current_ids = select(Asset.id).where(
        Asset.lifecycle_status == AssetLifecycleStatus.ENABLED
    )
    if scope.asset_ids is not None:
        current_ids = current_ids.where(Asset.id.in_(scope.asset_ids))
    snapshots = list(session.scalars(select(MonitoringSnapshot).where(MonitoringSnapshot.asset_id.in_(current_ids), MonitoringSnapshot.status == "online").order_by(MonitoringSnapshot.collected_at.desc()).limit(24)))
    return [{"collected_at": item.collected_at, "cpu_percent": float(item.metrics.get("cpu_percent", 0)), "memory_percent": float(item.metrics.get("memory_percent", 0)), "disk_percent": float(item.metrics.get("disk_percent", 0))} for item in reversed(snapshots)]


def _active_alerts(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    current_ids = select(Asset.id).where(Asset.lifecycle_status == AssetLifecycleStatus.ENABLED)
    if scope.asset_ids is not None:
        current_ids = current_ids.where(Asset.id.in_(scope.asset_ids))
    rows = session.execute(select(MonitoringAlertState, Asset.name).join(Asset, Asset.id == MonitoringAlertState.asset_id).where(MonitoringAlertState.asset_id.in_(current_ids), MonitoringAlertState.status == "triggered").order_by(MonitoringAlertState.triggered_at.desc()).limit(20)).all()
    return [{"asset_id": item.asset_id, "asset_name": name, "metric": item.metric, "value": item.last_value, "threshold": item.threshold, "triggered_at": item.triggered_at} for item, name in rows]


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
