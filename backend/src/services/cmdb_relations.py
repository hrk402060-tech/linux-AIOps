from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import (
    CmdbApplication, CmdbService, CmdbServiceAsset, ServiceAssetStatus,
)
from src.schemas.cmdb import ServiceAssetCreate, ServiceAssetUpdate
from src.services.cmdb_catalog import ensure_service_hierarchy_writable, get_item
from src.services.cmdb_common import (
    AuditActor, CmdbConflictError, CmdbReferenceError, add_audit,
    commit_or_conflict, flush_or_conflict, snapshot,
)


def list_relations(
    session: Session, service_id: str | None = None,
    asset_id: str | None = None, status: str = "active",
    visible_asset_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    statement = select(CmdbServiceAsset).order_by(CmdbServiceAsset.updated_at.desc())
    if service_id:
        statement = statement.where(CmdbServiceAsset.service_id == service_id)
    if asset_id:
        statement = statement.where(CmdbServiceAsset.asset_id == asset_id)
    if status != "all":
        try:
            statement = statement.where(CmdbServiceAsset.status == ServiceAssetStatus(status))
        except ValueError as exc:
            raise ValueError("无效的服务资产关系状态") from exc
    if visible_asset_ids is not None:
        statement = statement.where(CmdbServiceAsset.asset_id.in_(visible_asset_ids))
    return [relation_response(session, item) for item in session.scalars(statement)]


def create_relation(
    session: Session, service_id: str, payload: ServiceAssetCreate, actor: AuditActor,
) -> dict[str, object]:
    service = get_item(session, CmdbService, service_id)
    ensure_service_hierarchy_writable(session, service)
    asset = session.get(Asset, payload.asset_id)
    if asset is None:
        raise CmdbReferenceError("资产不存在")
    _ensure_asset_linkable(asset)
    relation = CmdbServiceAsset(
        id=str(uuid4()),
        service_id=service_id,
        asset_id=payload.asset_id,
        relation_role=payload.relation_role.strip(),
    )
    session.add(relation)
    flush_or_conflict(session, "服务与资产关系已存在，请恢复原关系")
    add_audit(session, "service_asset", relation.id, "link", None, snapshot(relation), actor)
    commit_or_conflict(session, "服务与资产关系已存在，请恢复原关系")
    return relation_response(session, relation)


def update_relation(
    session: Session, relation_id: str, payload: ServiceAssetUpdate, actor: AuditActor,
) -> dict[str, object]:
    relation = session.get(CmdbServiceAsset, relation_id)
    if relation is None:
        raise CmdbReferenceError("服务资产关系不存在")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise CmdbConflictError("未提供关系变更内容")
    before = snapshot(relation)
    previous_status = relation.status
    requested_status = values.get("status")
    if requested_status == ServiceAssetStatus.ACTIVE:
        service = get_item(session, CmdbService, relation.service_id)
        ensure_service_hierarchy_writable(session, service)
        asset = session.get(Asset, relation.asset_id)
        if asset is None:
            raise CmdbReferenceError("资产不存在")
        _ensure_asset_linkable(asset)
    if "relation_role" in values:
        relation.relation_role = str(values["relation_role"]).strip()
    if "status" in values:
        raw_status = values["status"]
        relation.status = (
            raw_status
            if isinstance(raw_status, ServiceAssetStatus)
            else ServiceAssetStatus(str(raw_status))
        )
        relation.unlinked_at = (
            datetime.now(timezone.utc)
            if relation.status == ServiceAssetStatus.UNLINKED else None
        )
        if relation.status == ServiceAssetStatus.ACTIVE:
            relation.linked_at = datetime.now(timezone.utc)
    action = "update"
    if relation.status != previous_status:
        action = "unlink" if relation.status == ServiceAssetStatus.UNLINKED else "link"
    session.flush()
    add_audit(session, "service_asset", relation.id, action, before, snapshot(relation), actor)
    session.commit()
    session.refresh(relation)
    return relation_response(session, relation)


def _ensure_asset_linkable(asset: Asset) -> None:
    if asset.lifecycle_status == AssetLifecycleStatus.ARCHIVED:
        raise CmdbConflictError("归档资产不能关联服务")


def relation_response(session: Session, relation: CmdbServiceAsset) -> dict[str, object]:
    service = get_item(session, CmdbService, relation.service_id)
    application = get_item(session, CmdbApplication, service.application_id)
    asset = session.get(Asset, relation.asset_id)
    if asset is None:
        raise CmdbReferenceError("关系引用的资产不存在")
    return {
        **snapshot(relation),
        "business_id": application.business_id,
        "application_id": application.id,
        "asset_name": asset.name,
        "asset_host": asset.host,
    }
