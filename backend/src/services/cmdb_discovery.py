from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import CmdbApplication, CmdbService, CmdbServiceAsset, ServiceAssetStatus
from src.models.monitoring import MonitoringSnapshot
from src.schemas.cmdb import CmdbDiscoveryConfirmRequest, ServiceAssetCreate, ServiceAssetUpdate, ServiceCreate
from src.services import cmdb_catalog, cmdb_relations
from src.services.cmdb_common import AuditActor, CmdbConflictError, CmdbReferenceError


def preview(session: Session, visible_asset_ids: set[str] | None = None) -> dict[str, object]:
    assets = session.scalars(
        select(Asset).where(Asset.lifecycle_status == AssetLifecycleStatus.ENABLED)
    ).all()
    if visible_asset_ids is not None:
        assets = [item for item in assets if item.id in visible_asset_ids]
    snapshots = _latest_snapshots(session, {item.id for item in assets})
    rows: list[dict[str, object]] = []
    for asset in assets:
        snapshot = snapshots.get(asset.id)
        if snapshot is None:
            continue
        rows.append({
            "asset_id": asset.id,
            "asset_name": asset.name,
            "host": asset.host,
            "observed_at": snapshot.collected_at,
            "status": snapshot.status,
            "services": _services(snapshot.metrics),
        })
    return {
        "generated_at": datetime.now(timezone.utc),
        "source": "latest_readonly_snapshot",
        "assets": rows,
    }


def confirm(
    session: Session,
    payload: CmdbDiscoveryConfirmRequest,
    actor: AuditActor,
) -> dict[str, object]:
    application = session.get(CmdbApplication, payload.application_id)
    if application is None:
        raise CmdbReferenceError("目标应用不存在")
    asset = session.get(Asset, payload.asset_id)
    if asset is None:
        raise CmdbReferenceError("目标资产不存在")
    code = _discovered_code(payload)
    service = session.scalar(select(CmdbService).where(
        CmdbService.application_id == application.id,
        CmdbService.code == code,
    ))
    created_service = service is None
    if service is None:
        service = cmdb_catalog.create_service(
            session,
            ServiceCreate(
                application_id=application.id,
                code=code,
                name=payload.service_name,
                description=f"由 {payload.source} 只读发现并经人工确认",
                service_type="discovered",
            ),
            actor,
        )
    elif service.lifecycle_status.value == "archived":
        raise CmdbConflictError("已归档的发现服务不能直接恢复，请先在 CMDB 中恢复")
    relation = session.scalar(select(CmdbServiceAsset).where(
        CmdbServiceAsset.service_id == service.id,
        CmdbServiceAsset.asset_id == asset.id,
    ))
    restored_relation = relation is not None and relation.status == ServiceAssetStatus.UNLINKED
    if relation is None:
        relation_data = cmdb_relations.create_relation(
            session,
            service.id,
            ServiceAssetCreate(asset_id=asset.id, relation_role="discovered"),
            actor,
        )
        relation_id = str(relation_data["id"])
        relation_status = ServiceAssetStatus.ACTIVE
    elif relation.status == ServiceAssetStatus.UNLINKED:
        relation_data = cmdb_relations.update_relation(
            session,
            relation.id,
            ServiceAssetUpdate(status=ServiceAssetStatus.ACTIVE, relation_role="discovered"),
            actor,
        )
        relation_id = str(relation_data["id"])
        relation_status = ServiceAssetStatus.ACTIVE
    else:
        relation_id = relation.id
        relation_status = relation.status
    return {
        "service_id": service.id,
        "service_code": service.code,
        "service_name": service.name,
        "relation_id": relation_id,
        "relation_status": relation_status,
        "created_service": created_service,
        "restored_relation": restored_relation,
    }


def _discovered_code(payload: CmdbDiscoveryConfirmRequest) -> str:
    digest = sha256(
        f"{payload.asset_id}:{payload.source}:{payload.service_name.casefold()}".encode()
    ).hexdigest()[:12].upper()
    return f"DISC-{digest}"


def _latest_snapshots(
    session: Session, asset_ids: set[str],
) -> dict[str, MonitoringSnapshot]:
    if not asset_ids:
        return {}
    rows = session.scalars(
        select(MonitoringSnapshot)
        .where(MonitoringSnapshot.asset_id.in_(asset_ids))
        .order_by(MonitoringSnapshot.collected_at.desc(), MonitoringSnapshot.id.desc())
    ).all()
    latest: dict[str, MonitoringSnapshot] = {}
    for item in rows:
        latest.setdefault(item.asset_id, item)
    return latest


def _services(metrics: dict[str, object]) -> list[dict[str, str]]:
    services: list[dict[str, str]] = []
    for item in metrics.get("systemd_services", []) if isinstance(metrics.get("systemd_services"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            services.append({"name": item["name"], "source": "systemd", "state": str(item.get("state", "unknown"))})
    for item in metrics.get("containers", []) if isinstance(metrics.get("containers"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            services.append({"name": item["name"], "source": "container", "state": str(item.get("status", "unknown"))})
    return services[:200]
