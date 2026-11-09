from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.monitoring import MonitoringSnapshot
from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding

ACTIVE_CASE_STATUSES = {"open", "pending_verification"}


def get_latest_snapshots(
    session: Session, asset_ids: list[str]
) -> dict[str, MonitoringSnapshot]:
    if not asset_ids:
        return {}
    ranked = select(
        MonitoringSnapshot.id.label("id"),
        func.row_number().over(
            partition_by=MonitoringSnapshot.asset_id,
            order_by=(
                MonitoringSnapshot.collected_at.desc(),
                MonitoringSnapshot.id.desc(),
            ),
        ).label("position"),
    ).where(MonitoringSnapshot.asset_id.in_(asset_ids)).subquery()
    statement = select(MonitoringSnapshot).join(
        ranked, MonitoringSnapshot.id == ranked.c.id
    ).where(ranked.c.position == 1)
    return {item.asset_id: item for item in session.scalars(statement)}


def get_latest_active_findings(
    session: Session, visible_asset_ids: set[str] | None = None,
) -> list[VulnerabilityFinding]:
    current_ids = select(Asset.id).where(
        Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED
    )
    if visible_asset_ids is not None:
        current_ids = current_ids.where(Asset.id.in_(visible_asset_ids))
    ranked = select(
        VulnerabilityFinding.id.label("id"),
        func.row_number().over(
            partition_by=(VulnerabilityFinding.asset_id, VulnerabilityFinding.cve_id),
            order_by=(
                VulnerabilityFinding.detected_at.desc(),
                VulnerabilityFinding.id.desc(),
            ),
        ).label("position"),
    ).where(VulnerabilityFinding.asset_id.in_(current_ids)).subquery()
    statement = select(VulnerabilityFinding).join(
        ranked, VulnerabilityFinding.id == ranked.c.id
    ).outerjoin(
        VulnerabilityCase, VulnerabilityFinding.case_id == VulnerabilityCase.id
    ).where(
        ranked.c.position == 1,
        or_(
            VulnerabilityFinding.case_id.is_(None),
            VulnerabilityCase.status.in_(ACTIVE_CASE_STATUSES),
        ),
    )
    return list(session.scalars(statement))


def get_finding_counts(
    findings: list[VulnerabilityFinding],
) -> dict[str, dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in findings:
        row = grouped.setdefault(item.asset_id, {
            "asset_id": item.asset_id,
            "asset_name": item.asset_name,
            "total": 0,
            "critical": 0,
            "high": 0,
        })
        row["total"] = int(row["total"]) + 1
        if item.severity in {"critical", "high"}:
            row[item.severity] = int(row[item.severity]) + 1
    return grouped
