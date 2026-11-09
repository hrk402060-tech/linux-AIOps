from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import (
    CmdbApplication,
    CmdbBusiness,
    CmdbLifecycle,
    CmdbService,
    CmdbServiceApi,
    CmdbServiceAsset,
    ServiceAssetStatus,
)
from src.models.incident import Incident
from src.models.monitoring import MonitoringAlertState
from src.models.probes import HttpProbe, HttpProbeRun
from src.models.vulnerability import VulnerabilityCase
from src.services.dashboard_access import DashboardScope, resolve_scope
from src.services.dashboard_tasks import failed_task_attention, task_summary
from src.services.probe_access import scope_context

ACTIVE_WORKFLOW_STATUSES = {"open", "acknowledged", "in_progress"}


def get_platform_snapshot(
    session: Session, scope: DashboardScope | None = None,
    monitoring_interval_seconds: int = 300,
) -> dict[str, object]:
    scope = scope if scope is not None else resolve_scope(session, None)
    service_health = _service_health(session, scope, monitoring_interval_seconds)
    probe_summary, probe_attention = _probe_snapshot(session, scope)
    return {
        "catalog": _catalog_summary(session, scope),
        "service_health": service_health,
        "operations": _operations(session, probe_summary, scope),
        "attention_items": _attention_items(session, probe_attention, scope),
    }


def _catalog_summary(session: Session, scope: DashboardScope) -> dict[str, int]:
    linked_assets = int(session.scalar(
        select(func.count(func.distinct(CmdbServiceAsset.asset_id)))
        .join(CmdbService, CmdbService.id == CmdbServiceAsset.service_id)
        .join(CmdbApplication, CmdbApplication.id == CmdbService.application_id)
        .join(CmdbBusiness, CmdbBusiness.id == CmdbApplication.business_id)
        .join(Asset, Asset.id == CmdbServiceAsset.asset_id)
        .where(
            CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE,
            Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED,
            CmdbService.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbApplication.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbBusiness.lifecycle_status == CmdbLifecycle.ACTIVE,
            *(() if scope.asset_ids is None else (Asset.id.in_(scope.asset_ids),)),
        )
    ) or 0)
    current_assets = int(session.scalar(
        select(func.count(Asset.id)).where(
            Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED
        ).where(*(() if scope.asset_ids is None else (Asset.id.in_(scope.asset_ids),)))
    ) or 0)
    return {
        "businesses": _active_count(session, CmdbBusiness, scope.catalog.business_ids),
        "applications": _active_count(session, CmdbApplication, scope.catalog.application_ids),
        "services": _active_count(session, CmdbService, scope.catalog.service_ids),
        "apis": _active_count(session, CmdbServiceApi, scope.catalog.api_ids),
        "linked_assets": linked_assets,
        "unlinked_assets": max(current_assets - linked_assets, 0),
    }


def _active_count(
    session: Session, model: type[object], ids: frozenset[str] | None,
) -> int:
    return int(session.scalar(
        select(func.count()).select_from(model).where(
            model.lifecycle_status == CmdbLifecycle.ACTIVE  # type: ignore[attr-defined]
        ).where(*(() if ids is None else (model.id.in_(ids),)))  # type: ignore[attr-defined]
    ) or 0)


def _service_health(
    session: Session, scope: DashboardScope, monitoring_interval_seconds: int,
) -> dict[str, object]:
    from src.services import observability

    snapshot = observability.get_services(
        session,
        visible_asset_ids=scope.asset_ids,
        monitoring_interval_seconds=monitoring_interval_seconds,
    )
    return {
        "summary": snapshot["summary"],
        "services": list(snapshot["services"])[:8],
    }


def _operations(
    session: Session, probe_summary: dict[str, int], scope: DashboardScope,
) -> dict[str, int]:
    return {
        "active_alerts": _count_asset_scoped(
            session, Alert, Alert.status.in_(ACTIVE_WORKFLOW_STATUSES), scope,
        ) + _count_asset_scoped(
            session, MonitoringAlertState, MonitoringAlertState.status == "triggered", scope,
        ),
        "severe_alerts": _count_asset_scoped(
            session, Alert,
            Alert.status.in_(ACTIVE_WORKFLOW_STATUSES)
            & Alert.severity.in_(("critical", "high")),
            scope,
        ),
        "active_incidents": _count_asset_scoped(
            session, Incident, Incident.status.in_(ACTIVE_WORKFLOW_STATUSES), scope,
        ),
        "severe_incidents": _count_asset_scoped(
            session, Incident,
            Incident.status.in_(ACTIVE_WORKFLOW_STATUSES)
            & Incident.severity.in_(("critical", "high")),
            scope,
        ),
        **task_summary(session, scope),
        **probe_summary,
        "pending_verification": _count_where(
            session, VulnerabilityCase,
            VulnerabilityCase.status == "pending_verification",
            scope,
        ),
    }


def _count_where(
    session: Session, model: type[object], condition: object, scope: DashboardScope,
) -> int:
    return int(session.scalar(
        select(func.count()).select_from(model).where(
            condition,
            *(() if scope.asset_ids is None else (model.asset_id.in_(scope.asset_ids),)),
        )
    ) or 0)


def _count_asset_scoped(
    session: Session, model: type[object], condition: object, scope: DashboardScope,
) -> int:
    statement = select(model).join(
            Asset, Asset.id == model.asset_id  # type: ignore[attr-defined]
        ).where(condition, Asset.lifecycle_status == AssetLifecycleStatus.ENABLED)
    if scope.asset_ids is not None:
        statement = statement.where(model.asset_id.in_(scope.asset_ids))
    if model is Incident:
        statement = scope.incidents(statement)
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _probe_snapshot(
    session: Session, scope: DashboardScope,
) -> tuple[dict[str, int], list[dict[str, object]]]:
    ranked_runs = scope_context(select(
        HttpProbeRun.id.label("run_id"),
        HttpProbeRun.probe_id.label("probe_id"),
        func.row_number().over(
            partition_by=HttpProbeRun.probe_id,
            order_by=(HttpProbeRun.checked_at.desc(), HttpProbeRun.id.desc()),
        ).label("position"),
    ), HttpProbeRun, scope.asset_ids).subquery()
    statement = scope_context(
        select(HttpProbe, HttpProbeRun)
        .join(Asset, Asset.id == HttpProbe.asset_id)
        .outerjoin(
            ranked_runs,
            and_(ranked_runs.c.probe_id == HttpProbe.id, ranked_runs.c.position == 1),
        )
        .outerjoin(HttpProbeRun, HttpProbeRun.id == ranked_runs.c.run_id)
        .where(
            HttpProbe.status == "active",
            Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
        )
        .order_by(HttpProbe.created_at.desc(), HttpProbe.id.desc())
        .limit(500), HttpProbe, scope.asset_ids,
    )
    rows = session.execute(statement).all()
    attention = [
        {
            "id": run.id, "kind": "probe", "title": probe.name,
            "detail": run.error_code or run.status, "severity": "high",
            "status": run.status, "occurred_at": run.checked_at,
            "asset_id": probe.asset_id, "asset_name": None, "target": "probes",
        }
        for probe, run in rows if run is not None and run.status != "healthy"
    ]
    return ({
        "active_probes": len(rows),
        "failing_probes": len(attention),
        "unobserved_probes": sum(run is None for _, run in rows),
    }, attention)


def _attention_items(
    session: Session, probe_attention: list[dict[str, object]], scope: DashboardScope,
) -> list[dict[str, object]]:
    items = [*probe_attention]
    items.extend(_alert_attention(session, scope))
    items.extend(_monitoring_attention(session, scope))
    items.extend(_incident_attention(session, scope))
    items.extend(failed_task_attention(session, scope))
    items.sort(key=lambda item: _aware(item.get("occurred_at")), reverse=True)
    return items[:12]


def _alert_attention(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    rows = session.scalars(
        select(Alert).join(Asset, Asset.id == Alert.asset_id).where(
            Alert.status.in_(ACTIVE_WORKFLOW_STATUSES),
            Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
            *(() if scope.asset_ids is None else (Alert.asset_id.in_(scope.asset_ids),)),
        ).order_by(Alert.updated_at.desc()).limit(8)
    )
    return [{
        "id": item.id, "kind": "alert", "title": item.title,
        "detail": f"{item.asset_name} · {item.status}",
        "severity": item.severity, "status": item.status,
        "occurred_at": item.updated_at, "asset_id": item.asset_id,
        "asset_name": item.asset_name, "target": "alerts",
    } for item in rows]


def _monitoring_attention(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    rows = session.execute(
        select(MonitoringAlertState, Asset.name).join(
            Asset, Asset.id == MonitoringAlertState.asset_id
        ).where(
            MonitoringAlertState.status == "triggered",
            Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
            *(() if scope.asset_ids is None else (MonitoringAlertState.asset_id.in_(scope.asset_ids),)),
        ).order_by(MonitoringAlertState.triggered_at.desc()).limit(8)
    ).all()
    return [{
        "id": item.id, "kind": "monitoring",
        "title": f"{name} · {item.metric}",
        "detail": f"{item.last_value:.1f} / {item.threshold:.1f}",
        "severity": "medium", "status": item.status,
        "occurred_at": item.triggered_at, "asset_id": item.asset_id,
        "asset_name": name, "target": "observability",
    } for item, name in rows]


def _incident_attention(session: Session, scope: DashboardScope) -> list[dict[str, object]]:
    rows = session.scalars(
        scope.incidents(select(Incident).join(Asset, Asset.id == Incident.asset_id).where(
            Incident.status.in_(ACTIVE_WORKFLOW_STATUSES),
            Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
        )).order_by(Incident.updated_at.desc()).limit(8)
    )
    return [{
        "id": item.id, "kind": "incident", "title": item.title,
        "detail": f"{item.asset_name} · {item.status}",
        "severity": item.severity, "status": item.status,
        "occurred_at": item.updated_at, "asset_id": item.asset_id,
        "asset_name": item.asset_name, "target": "incidents",
    } for item in rows]


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
