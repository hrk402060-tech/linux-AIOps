from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.alert import Alert
from src.models.monitoring import MonitoringPolicy, MonitoringSnapshot
from src.schemas.monitoring import MonitoringPolicyResponse
from src.services.dashboard_queries import (
    get_finding_counts, get_latest_active_findings, get_latest_snapshots,
)
from src.services.dashboard_platform import ACTIVE_WORKFLOW_STATUSES
from src.services.monitoring_rules import aware, is_current, metric_value, resource_statuses
from src.services.observability import freshness_threshold_seconds

TREND_BUCKETS = 24


class MonitoringFleetError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_fleet(
    session: Session, visible_asset_ids: set[str] | None,
    monitoring_interval_seconds: int,
) -> dict[str, object]:
    now = utc_now()
    stale_after = freshness_threshold_seconds(monitoring_interval_seconds)
    assets = _assets(session, visible_asset_ids)
    asset_ids = [asset.id for asset in assets]
    latest = get_latest_snapshots(session, asset_ids)
    policies = _policies(session, asset_ids)
    duration = max((policy.severity_rules.duration_minutes for policy in policies.values()), default=5)
    history = _history(session, asset_ids, now - timedelta(
        minutes=max(5, duration), seconds=stale_after,
    ), now)
    alerts = _alert_counts(session, asset_ids)
    findings = get_finding_counts(get_latest_active_findings(session, set(asset_ids)))
    return {
        "generated_at": now, "stale_after_seconds": stale_after,
        "assets": [
            _asset_row(
                asset, latest.get(asset.id), policies[asset.id], history.get(asset.id, []),
                alerts.get(asset.id, 0), findings.get(asset.id, {}), now, stale_after,
            )
            for asset in assets
        ],
    }


def get_trends(
    session: Session, asset_ids: list[str], hours: int, monitoring_interval_seconds: int,
) -> dict[str, object]:
    now = utc_now()
    stale_after = freshness_threshold_seconds(monitoring_interval_seconds)
    bucket_seconds = hours * 3600 // TREND_BUCKETS
    result: dict[str, object] = {
        "generated_at": now, "stale_after_seconds": stale_after,
        "bucket_seconds": bucket_seconds, "buckets": [],
    }
    if not asset_ids:
        return result
    assets = _assets(session, set(asset_ids))
    if {asset.id for asset in assets} != set(asset_ids):
        raise MonitoringFleetError("目标资产不存在或已归档")
    latest = get_latest_snapshots(session, asset_ids)
    eligible = [
        asset.id for asset in assets
        if asset.lifecycle_status == AssetLifecycleStatus.ENABLED
        and is_current(latest.get(asset.id), now, stale_after)
        and any(metric_value(latest[asset.id].metrics, resource) is not None
                for resource in ("cpu", "memory"))
    ]
    start = now - timedelta(hours=hours)
    histories = _trend_history(session, eligible, start, now, bucket_seconds)
    result["buckets"] = _trend_buckets(histories, start, bucket_seconds)
    return result


def _assets(session: Session, asset_ids: set[str] | None) -> list[Asset]:
    statement = select(Asset).where(
        Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED,
    )
    if asset_ids is not None:
        statement = statement.where(Asset.id.in_(asset_ids))
    return list(session.scalars(statement.order_by(Asset.name, Asset.id)))


def _policies(session: Session, asset_ids: list[str]) -> dict[str, MonitoringPolicyResponse]:
    stored = {
        policy.asset_id: MonitoringPolicyResponse.model_validate(policy)
        for policy in session.scalars(select(MonitoringPolicy).where(
            MonitoringPolicy.asset_id.in_(asset_ids),
        ))
    }
    return {
        asset_id: stored.get(asset_id) or MonitoringPolicyResponse(
            asset_id=asset_id, cpu_threshold=85, memory_threshold=85,
            swap_threshold=80, disk_threshold=85, inode_threshold=85,
            load_threshold=8, duration_minutes=5, recovery_notify=True,
            maintenance_start=None, maintenance_end=None, updated_at=None,
        )
        for asset_id in asset_ids
    }


def _history(
    session: Session, asset_ids: list[str], start: datetime, end: datetime,
) -> dict[str, list[MonitoringSnapshot]]:
    if not asset_ids:
        return {}
    statement = select(MonitoringSnapshot).where(
        MonitoringSnapshot.asset_id.in_(asset_ids),
        MonitoringSnapshot.collected_at >= start,
        MonitoringSnapshot.collected_at <= end,
    ).order_by(MonitoringSnapshot.collected_at.desc(), MonitoringSnapshot.id.desc())
    grouped: dict[str, list[MonitoringSnapshot]] = {}
    for snapshot in session.scalars(statement):
        grouped.setdefault(snapshot.asset_id, []).append(snapshot)
    return grouped


def _trend_history(
    session: Session, asset_ids: list[str], start: datetime,
    end: datetime, bucket_seconds: int,
) -> dict[str, list[MonitoringSnapshot]]:
    if not asset_ids:
        return {}
    # Compare timestamps directly so bucket boundaries retain subsecond precision
    # on both SQLite and PostgreSQL without loading the entire time range.
    bucket = case(*[
        (
            MonitoringSnapshot.collected_at
            < start + timedelta(seconds=(index + 1) * bucket_seconds),
            index,
        )
        for index in range(TREND_BUCKETS - 1)
    ], else_=TREND_BUCKETS - 1)
    ranked = select(
        MonitoringSnapshot.id.label("id"),
        func.row_number().over(
            partition_by=(MonitoringSnapshot.asset_id, bucket),
            order_by=(
                MonitoringSnapshot.collected_at.desc(),
                MonitoringSnapshot.id.desc(),
            ),
        ).label("position"),
    ).where(
        MonitoringSnapshot.asset_id.in_(asset_ids),
        MonitoringSnapshot.collected_at >= start,
        MonitoringSnapshot.collected_at <= end,
    ).subquery()
    statement = select(MonitoringSnapshot).join(
        ranked, MonitoringSnapshot.id == ranked.c.id,
    ).where(ranked.c.position == 1).order_by(
        MonitoringSnapshot.collected_at.desc(), MonitoringSnapshot.id.desc(),
    )
    grouped: dict[str, list[MonitoringSnapshot]] = {}
    for snapshot in session.scalars(statement):
        grouped.setdefault(snapshot.asset_id, []).append(snapshot)
    return grouped


def _alert_counts(session: Session, asset_ids: list[str]) -> dict[str, int]:
    return dict(session.execute(
        select(Alert.asset_id, func.count(Alert.id))
        .where(
            Alert.asset_id.in_(asset_ids),
            Alert.status.in_(ACTIVE_WORKFLOW_STATUSES),
        ).group_by(Alert.asset_id),
    ).all())


def _asset_row(
    asset: Asset, snapshot: MonitoringSnapshot | None, policy: MonitoringPolicyResponse,
    history: list[MonitoringSnapshot], alerts: int, findings: dict[str, object],
    now: datetime, stale_after: int,
) -> dict[str, object]:
    return {
        "asset_id": asset.id, "asset_name": asset.name, "host": asset.host,
        "group_name": asset.group_name, "lifecycle_status": asset.lifecycle_status.value,
        "latest_snapshot": snapshot,
        "stale": bool(snapshot and not 0 <= (
            now - aware(snapshot.collected_at)
        ).total_seconds() <= stale_after),
        "active_alerts": alerts, "pending_vulnerabilities": int(findings.get("total", 0)),
        "critical": int(findings.get("critical", 0)), "high": int(findings.get("high", 0)),
        "policy": policy,
        "resource_statuses": resource_statuses(
            snapshot, history, policy.severity_rules,
            asset.lifecycle_status == AssetLifecycleStatus.ENABLED, now, stale_after,
        ),
    }


def _trend_buckets(
    histories: dict[str, list[MonitoringSnapshot]], start: datetime, bucket_seconds: int,
) -> list[dict[str, object]]:
    buckets: list[dict[str, MonitoringSnapshot]] = [{} for _ in range(TREND_BUCKETS)]
    for asset_id, snapshots in histories.items():
        for snapshot in snapshots:
            offset = (aware(snapshot.collected_at) - start).total_seconds()
            position = min(int(offset // bucket_seconds), TREND_BUCKETS - 1)
            buckets[position].setdefault(asset_id, snapshot)
    points = []
    for index, snapshots in enumerate(buckets):
        values = {
            resource: [
                value for snapshot in snapshots.values()
                if snapshot.status == "online"
                and (value := metric_value(snapshot.metrics, resource)) is not None
            ]
            for resource in ("cpu", "memory")
        }
        points.append({
            "collected_at": start + timedelta(seconds=index * bucket_seconds),
            "cpu_percent": _average(values["cpu"]),
            "memory_percent": _average(values["memory"]),
            "cpu_asset_count": len(values["cpu"]),
            "memory_asset_count": len(values["memory"]),
        })
    return points


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
