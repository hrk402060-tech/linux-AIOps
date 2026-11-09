from datetime import datetime, timezone
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.models.auth import User
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbLifecycle, CmdbService, CmdbServiceApi,
)
from src.schemas.cmdb import (
    ApiCreate, ApiUpdate, ApplicationCreate, BusinessCreate, CatalogUpdate,
    ServiceCreate, ServiceUpdate,
)
from src.services.cmdb_common import (
    AuditActor, CmdbConflictError, CmdbReferenceError, add_audit,
    commit_or_conflict, flush_or_conflict, lifecycle_action, snapshot,
)

CatalogModel = TypeVar(
    "CatalogModel", CmdbBusiness, CmdbApplication, CmdbService, CmdbServiceApi
)


def list_items(
    session: Session, model: type[CatalogModel], lifecycle: str,
    query: str | None, parent_field: str | None = None, parent_id: str | None = None,
) -> list[CatalogModel]:
    statement = select(model).order_by(model.name.asc())
    if lifecycle != "all":
        try:
            statement = statement.where(model.lifecycle_status == CmdbLifecycle(lifecycle))
        except ValueError as exc:
            raise ValueError("无效的 CMDB 生命周期筛选") from exc
    if query:
        needle = f"%{query.strip()}%"
        statement = statement.where(or_(model.code.ilike(needle), model.name.ilike(needle)))
    if parent_field and parent_id:
        statement = statement.where(getattr(model, parent_field) == parent_id)
    return list(session.scalars(statement))


def get_item(session: Session, model: type[CatalogModel], item_id: str) -> CatalogModel:
    item = session.get(model, item_id)
    if item is None:
        raise CmdbReferenceError("CMDB 记录不存在")
    return item


def create_business(
    session: Session, payload: BusinessCreate, actor: AuditActor
) -> CmdbBusiness:
    _ensure_owner(session, payload.owner_user_id)
    item = CmdbBusiness(id=str(uuid4()), **payload.model_dump())
    session.add(item)
    flush_or_conflict(session, "业务编码已存在")
    add_audit(session, "business", item.id, "create", None, snapshot(item), actor)
    commit_or_conflict(session, "业务编码已存在")
    session.refresh(item)
    return item


def create_application(
    session: Session, payload: ApplicationCreate, actor: AuditActor
) -> CmdbApplication:
    parent = get_item(session, CmdbBusiness, payload.business_id)
    ensure_entity_hierarchy_writable(session, parent)
    _ensure_owner(session, payload.owner_user_id)
    item = CmdbApplication(id=str(uuid4()), **payload.model_dump())
    return _create_child(session, item, "application", "同一业务下应用编码已存在", actor)


def create_service(
    session: Session, payload: ServiceCreate, actor: AuditActor
) -> CmdbService:
    parent = get_item(session, CmdbApplication, payload.application_id)
    ensure_entity_hierarchy_writable(session, parent)
    _ensure_owner(session, payload.owner_user_id)
    item = CmdbService(id=str(uuid4()), **payload.model_dump())
    return _create_child(session, item, "service", "同一应用下服务编码已存在", actor)


def create_api(
    session: Session, payload: ApiCreate, actor: AuditActor
) -> CmdbServiceApi:
    parent = get_item(session, CmdbService, payload.service_id)
    ensure_service_hierarchy_writable(session, parent)
    _ensure_owner(session, payload.owner_user_id)
    item = CmdbServiceApi(id=str(uuid4()), **payload.model_dump())
    return _create_child(session, item, "api", "同一服务下 API 编码已存在", actor)


def update_item(
    session: Session, item: CatalogModel,
    payload: CatalogUpdate | ServiceUpdate | ApiUpdate,
    entity_type: str, actor: AuditActor,
) -> CatalogModel:
    values = payload.model_dump(exclude_unset=True)
    ensure_ancestors_writable(session, item)
    _validate_update(session, item, values)
    before = snapshot(item)
    old_lifecycle = item.lifecycle_status
    for field, value in values.items():
        if field != "lifecycle_reason":
            setattr(item, field, value)
    if "lifecycle_reason" in values or "lifecycle_status" in values:
        item.lifecycle_reason = values.get("lifecycle_reason")
    if item.lifecycle_status == CmdbLifecycle.ARCHIVED:
        item.archived_at = datetime.now(timezone.utc)
    elif "lifecycle_status" in values:
        item.archived_at = None
    action = lifecycle_action(old_lifecycle, item.lifecycle_status)
    flush_or_conflict(session, "当前父级范围内编码已存在")
    add_audit(session, entity_type, item.id, action, before, snapshot(item), actor)
    commit_or_conflict(session, "当前父级范围内编码已存在")
    session.refresh(item)
    return item


def ancestor_ids(
    session: Session, item: CmdbService | CmdbServiceApi
) -> tuple[str, str | None]:
    service = item if isinstance(item, CmdbService) else get_item(
        session, CmdbService, item.service_id
    )
    application = get_item(session, CmdbApplication, service.application_id)
    return application.business_id, service.application_id


def ensure_service_hierarchy_writable(
    session: Session, service: CmdbService
) -> None:
    ensure_entity_hierarchy_writable(session, service)


def ensure_entity_hierarchy_writable(
    session: Session, item: CatalogModel
) -> None:
    ensure_ancestors_writable(session, item)
    _ensure_parent_writable(item)


def ensure_ancestors_writable(session: Session, item: CatalogModel) -> None:
    ancestors: list[CatalogModel] = []
    if isinstance(item, CmdbApplication):
        ancestors.append(get_item(session, CmdbBusiness, item.business_id))
    elif isinstance(item, CmdbService):
        application = get_item(session, CmdbApplication, item.application_id)
        ancestors.extend((get_item(session, CmdbBusiness, application.business_id), application))
    elif isinstance(item, CmdbServiceApi):
        service = get_item(session, CmdbService, item.service_id)
        application = get_item(session, CmdbApplication, service.application_id)
        ancestors.extend((
            get_item(session, CmdbBusiness, application.business_id), application, service,
        ))
    for ancestor in ancestors:
        _ensure_parent_writable(ancestor)


def _create_child(
    session: Session, item: CatalogModel, entity_type: str,
    conflict_message: str, actor: AuditActor,
) -> CatalogModel:
    session.add(item)
    flush_or_conflict(session, conflict_message)
    add_audit(session, entity_type, item.id, "create", None, snapshot(item), actor)
    commit_or_conflict(session, conflict_message)
    session.refresh(item)
    return item


def _validate_update(
    session: Session, item: CatalogModel, values: dict[str, object]
) -> None:
    if "owner_user_id" in values:
        owner = values["owner_user_id"]
        _ensure_owner(session, owner if isinstance(owner, str) else None)
    if "lifecycle_status" in values and not values.get("lifecycle_reason"):
        raise CmdbConflictError("生命周期变更必须填写原因")
    if item.lifecycle_status == CmdbLifecycle.ARCHIVED and "lifecycle_status" not in values:
        raise CmdbConflictError("归档记录必须先恢复后才能编辑")


def _ensure_parent_writable(parent: object) -> None:
    if getattr(parent, "lifecycle_status") == CmdbLifecycle.ARCHIVED:
        raise CmdbConflictError("归档父级不能新增子级")


def _ensure_owner(session: Session, owner_user_id: str | None) -> None:
    if owner_user_id is not None and session.get(User, owner_user_id) is None:
        raise CmdbReferenceError("责任人用户不存在")
