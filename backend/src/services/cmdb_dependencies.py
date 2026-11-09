from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.models.cmdb import (
    CmdbService, CmdbServiceDependency, ServiceDependencyStatus,
)
from src.schemas.cmdb import ServiceDependencyCreate, ServiceDependencyUpdate
from src.services.cmdb_catalog import get_item, ensure_service_hierarchy_writable
from src.services.cmdb_common import (
    AuditActor, CmdbConflictError, CmdbReferenceError, add_audit,
    commit_or_conflict, flush_or_conflict, snapshot,
)


def list_dependencies(
    session: Session,
    service_id: str,
    visible_service_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, object]]:
    statement = select(CmdbServiceDependency).where(
        or_(
            CmdbServiceDependency.source_service_id == service_id,
            CmdbServiceDependency.target_service_id == service_id,
        ),
    ).order_by(CmdbServiceDependency.updated_at.desc())
    rows = session.scalars(statement).all()
    if visible_service_ids is not None:
        rows = [
            row for row in rows
            if row.source_service_id in visible_service_ids
            and row.target_service_id in visible_service_ids
        ]
    return [_response(session, row) for row in rows]


def create_dependency(
    session: Session,
    source_service_id: str,
    payload: ServiceDependencyCreate,
    actor: AuditActor,
) -> dict[str, object]:
    source = _writable_service(session, source_service_id)
    target = _writable_service(session, payload.target_service_id)
    if source.id == target.id:
        raise CmdbConflictError("服务不能依赖自身")
    dependency = CmdbServiceDependency(
        source_service_id=source.id,
        target_service_id=target.id,
        dependency_type=payload.dependency_type,
        description=payload.description,
        status=ServiceDependencyStatus.ACTIVE,
    )
    session.add(dependency)
    flush_or_conflict(session, "该服务依赖关系已存在")
    add_audit(
        session, "service_dependency", dependency.id, "link",
        None, snapshot(dependency), actor,
    )
    commit_or_conflict(session, "该服务依赖关系已存在")
    return _response(session, dependency)


def update_dependency(
    session: Session,
    dependency_id: str,
    payload: ServiceDependencyUpdate,
    actor: AuditActor,
) -> dict[str, object]:
    dependency = session.get(CmdbServiceDependency, dependency_id)
    if dependency is None:
        raise CmdbReferenceError("服务依赖关系不存在")
    _writable_service(session, dependency.source_service_id)
    _writable_service(session, dependency.target_service_id)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise CmdbConflictError("未提供依赖关系变更内容")
    before = snapshot(dependency)
    if "status" in values:
        dependency.status = values["status"]
    if "description" in values:
        dependency.description = values["description"]
    session.flush()
    add_audit(
        session, "service_dependency", dependency.id,
        "disable" if dependency.status == ServiceDependencyStatus.DISABLED else "update",
        before, snapshot(dependency), actor,
    )
    session.commit()
    session.refresh(dependency)
    return _response(session, dependency)


def _writable_service(session: Session, service_id: str) -> CmdbService:
    service = get_item(session, CmdbService, service_id)
    ensure_service_hierarchy_writable(session, service)
    return service


def _response(session: Session, dependency: CmdbServiceDependency) -> dict[str, object]:
    source = session.get(CmdbService, dependency.source_service_id)
    target = session.get(CmdbService, dependency.target_service_id)
    if source is None or target is None:
        raise CmdbReferenceError("依赖关系引用的服务不存在")
    return {
        **snapshot(dependency),
        "source_service_name": source.name,
        "target_service_name": target.name,
    }
