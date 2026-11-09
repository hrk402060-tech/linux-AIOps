from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceApi
from src.schemas.cmdb import (
    ApiCreate, ApiResponse, ApiUpdate, ApplicationCreate, ApplicationResponse,
    BusinessCreate, BusinessResponse, CatalogUpdate, CmdbTreeResponse,
    ServiceAssetCreate, ServiceAssetResponse, ServiceAssetUpdate, ServiceCreate,
    ServiceDependencyCreate, ServiceDependencyResponse, ServiceDependencyUpdate,
    ServiceResponse, ServiceUpdate, CmdbDiscoveryConfirmRequest,
    CmdbDiscoveryConfirmResponse, CmdbDiscoveryResponse,
)
from src.services import cmdb_catalog as catalog
from src.services import cmdb_access
from src.services import cmdb_relations as relations
from src.services import cmdb_discovery
from src.services import cmdb_dependencies as dependencies
from src.services.cmdb_common import (
    AuditActor, CmdbConflictError, CmdbReferenceError, make_actor,
)

router = APIRouter(prefix="/cmdb", tags=["cmdb"])


def _actor(principal: AuthPrincipal, trace_id: str | None) -> AuditActor:
    return make_actor(principal.user_id, principal.username, trace_id)


def _raise_domain_error(exc: ValueError) -> None:
    if isinstance(exc, CmdbConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, CmdbReferenceError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _raise_access_error(exc: cmdb_access.CmdbAccessError) -> None:
    raise HTTPException(status_code=403, detail=str(exc)) from exc


def _enforce_archive_permission(
    principal: AuthPrincipal, current: object, lifecycle_status: object | None
) -> None:
    current_status = getattr(current, "lifecycle_status")
    is_archive_change = lifecycle_status is not None and (
        str(getattr(lifecycle_status, "value", lifecycle_status)) == "archived"
        or str(getattr(current_status, "value", current_status)) == "archived"
    )
    if is_archive_change and principal.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可归档或恢复 CMDB 记录")


@router.get("/tree", response_model=CmdbTreeResponse)
def read_tree(
    lifecycle: str = Query("active"), query: str | None = None,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    visibility = cmdb_access.for_principal(session, principal)
    try:
        businesses = catalog.list_items(session, CmdbBusiness, lifecycle, None)
        applications = catalog.list_items(session, CmdbApplication, lifecycle, None)
        services = catalog.list_items(session, CmdbService, lifecycle, None)
        apis = catalog.list_items(session, CmdbServiceApi, lifecycle, None)
        relation_rows = relations.list_relations(
            session, status="all", visible_asset_ids=visibility.asset_ids
        )
    except ValueError as exc:
        _raise_domain_error(exc)
    businesses = [item for item in businesses if cmdb_access.allows(item, visibility)]
    applications = [item for item in applications if cmdb_access.allows(item, visibility)]
    services = [item for item in services if cmdb_access.allows(item, visibility)]
    apis = [item for item in apis if cmdb_access.allows(item, visibility)]
    app_rows = [_application_dict(item) for item in applications]
    service_rows = [_service_dict(session, item) for item in services]
    api_rows = [_api_dict(session, item) for item in apis]
    for service in service_rows:
        service["apis"] = [item for item in api_rows if item["service_id"] == service["id"]]
        service["assets"] = [item for item in relation_rows if item["service_id"] == service["id"]]
    for application in app_rows:
        application["services"] = [
            item for item in service_rows if item["application_id"] == application["id"]
        ]
    rows: list[dict[str, object]] = []
    for business in businesses:
        item = _business_dict(business)
        item["applications"] = [
            app for app in app_rows if app["business_id"] == business.id
        ]
        if not query or _matches_tree(item, query):
            rows.append(item)
    return {"businesses": rows}


@router.get("/discovery/preview", response_model=CmdbDiscoveryResponse)
def read_discovery_preview(
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    visibility = cmdb_access.for_principal(session, principal)
    return cmdb_discovery.preview(session, visibility.asset_ids)


@router.post(
    "/discovery/confirm",
    response_model=CmdbDiscoveryConfirmResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_discovery(
    payload: CmdbDiscoveryConfirmRequest,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    visibility = cmdb_access.for_principal(session, principal)
    try:
        cmdb_access.ensure_asset_visible(payload.asset_id, visibility)
        application = _get(session, CmdbApplication, payload.application_id, principal)
        cmdb_access.ensure_writable(session, application, visibility)
        return cmdb_discovery.confirm(
            session, payload, _actor(principal, trace_id),
        )
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.get("/businesses", response_model=list[BusinessResponse])
def read_businesses(
    lifecycle: str = "active", query: str | None = None,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[CmdbBusiness]:
    return _list(session, CmdbBusiness, lifecycle, query, principal=principal)


@router.get("/businesses/{item_id}", response_model=BusinessResponse)
def read_business(
    item_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> CmdbBusiness:
    return _get(session, CmdbBusiness, item_id, principal)


@router.post("/businesses", response_model=BusinessResponse, status_code=status.HTTP_201_CREATED)
def add_business(
    payload: BusinessCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session), trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> CmdbBusiness:
    try:
        cmdb_access.ensure_root_create(cmdb_access.for_principal(session, principal))
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    return _call(catalog.create_business, session, payload, _actor(principal, trace_id))


@router.patch("/businesses/{item_id}", response_model=BusinessResponse)
def change_business(
    item_id: str, payload: CatalogUpdate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> CmdbBusiness:
    return _change(session, CmdbBusiness, item_id, payload, "business", principal, trace_id)


@router.get("/applications", response_model=list[ApplicationResponse])
def read_applications(
    business_id: str | None = None, lifecycle: str = "active", query: str | None = None,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[CmdbApplication]:
    return _list(
        session, CmdbApplication, lifecycle, query, "business_id", business_id, principal
    )


@router.get("/applications/{item_id}", response_model=ApplicationResponse)
def read_application(
    item_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> CmdbApplication:
    return _get(session, CmdbApplication, item_id, principal)


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
def add_application(
    payload: ApplicationCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session), trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> CmdbApplication:
    _require_writable_parent(session, principal, CmdbBusiness, payload.business_id)
    return _call(catalog.create_application, session, payload, _actor(principal, trace_id))


@router.patch("/applications/{item_id}", response_model=ApplicationResponse)
def change_application(
    item_id: str, payload: CatalogUpdate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> CmdbApplication:
    return _change(session, CmdbApplication, item_id, payload, "application", principal, trace_id)


@router.get("/services", response_model=list[ServiceResponse])
def read_services(
    application_id: str | None = None, lifecycle: str = "active", query: str | None = None,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return [_service_dict(session, item) for item in _list(
        session, CmdbService, lifecycle, query, "application_id", application_id, principal
    )]


@router.get("/services/{item_id}", response_model=ServiceResponse)
def read_service(
    item_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return _service_dict(session, _get(session, CmdbService, item_id, principal))


@router.post("/services", response_model=ServiceResponse, status_code=201)
def add_service(
    payload: ServiceCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session), trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    _require_writable_parent(session, principal, CmdbApplication, payload.application_id)
    item = _call(catalog.create_service, session, payload, _actor(principal, trace_id))
    return _service_dict(session, item)


@router.patch("/services/{item_id}", response_model=ServiceResponse)
def change_service(
    item_id: str, payload: ServiceUpdate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    item = _change(session, CmdbService, item_id, payload, "service", principal, trace_id)
    return _service_dict(session, item)


@router.get(
    "/services/{service_id}/dependencies",
    response_model=list[ServiceDependencyResponse],
)
def read_service_dependencies(
    service_id: str,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        visibility = cmdb_access.for_principal(session, principal)
        service = _get(session, CmdbService, service_id, principal)
        if visibility.asset_ids is None:
            visible_services = None
        else:
            visible_services = set(visibility.service_ids or ())
        return dependencies.list_dependencies(session, service.id, visible_services)
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.post(
    "/services/{service_id}/dependencies",
    response_model=ServiceDependencyResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_service_dependency(
    service_id: str,
    payload: ServiceDependencyCreate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    visibility = cmdb_access.for_principal(session, principal)
    try:
        cmdb_access.ensure_visible(
            _get(session, CmdbService, service_id, principal), visibility
        )
        cmdb_access.ensure_visible(
            _get(session, CmdbService, payload.target_service_id, principal), visibility
        )
        return dependencies.create_dependency(
            session, service_id, payload, _actor(principal, trace_id),
        )
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.patch(
    "/service-dependencies/{dependency_id}",
    response_model=ServiceDependencyResponse,
)
def change_service_dependency(
    dependency_id: str,
    payload: ServiceDependencyUpdate,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    try:
        dependency = session.get(dependencies.CmdbServiceDependency, dependency_id)
        if dependency is None:
            raise dependencies.CmdbReferenceError("服务依赖关系不存在")
        visibility = cmdb_access.for_principal(session, principal)
        cmdb_access.ensure_visible(
            _get(session, CmdbService, dependency.source_service_id, principal), visibility
        )
        cmdb_access.ensure_visible(
            _get(session, CmdbService, dependency.target_service_id, principal), visibility
        )
        return dependencies.update_dependency(
            session, dependency_id, payload, _actor(principal, trace_id),
        )
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.get("/apis", response_model=list[ApiResponse])
def read_apis(
    service_id: str | None = None, lifecycle: str = "active", query: str | None = None,
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return [_api_dict(session, item) for item in _list(
        session, CmdbServiceApi, lifecycle, query, "service_id", service_id, principal
    )]


@router.get("/apis/{item_id}", response_model=ApiResponse)
def read_api(
    item_id: str, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return _api_dict(session, _get(session, CmdbServiceApi, item_id, principal))


@router.post("/apis", response_model=ApiResponse, status_code=201)
def add_api(
    payload: ApiCreate, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session), trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    _require_writable_parent(session, principal, CmdbService, payload.service_id)
    item = _call(catalog.create_api, session, payload, _actor(principal, trace_id))
    return _api_dict(session, item)


@router.patch("/apis/{item_id}", response_model=ApiResponse)
def change_api(
    item_id: str, payload: ApiUpdate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    item = _change(session, CmdbServiceApi, item_id, payload, "api", principal, trace_id)
    return _api_dict(session, item)


@router.get("/service-assets", response_model=list[ServiceAssetResponse])
def read_service_assets(
    service_id: str | None = None, asset_id: str | None = None, status_filter: str = Query("active", alias="status"),
    principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        visibility = cmdb_access.for_principal(session, principal)
        if service_id:
            _require_parent(session, principal, CmdbService, service_id)
        if asset_id:
            cmdb_access.ensure_asset_visible(asset_id, visibility)
        return relations.list_relations(
            session, service_id, asset_id, status_filter, visibility.asset_ids
        )
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.post("/services/{service_id}/assets", response_model=ServiceAssetResponse, status_code=201)
def add_service_asset(
    service_id: str, payload: ServiceAssetCreate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    _require_writable_parent(session, principal, CmdbService, service_id)
    try:
        cmdb_access.ensure_asset_visible(
            payload.asset_id, cmdb_access.for_principal(session, principal)
        )
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    return _call(relations.create_relation, session, service_id, payload, _actor(principal, trace_id))


@router.patch("/service-assets/{relation_id}", response_model=ServiceAssetResponse)
def change_service_asset(
    relation_id: str, payload: ServiceAssetUpdate,
    principal: AuthPrincipal = Depends(require_admin), session: Session = Depends(get_session),
    trace_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, object]:
    relation = session.get(relations.CmdbServiceAsset, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="服务资产关系不存在")
    visibility = cmdb_access.for_principal(session, principal)
    try:
        cmdb_access.ensure_asset_visible(relation.asset_id, visibility)
        _require_parent(session, principal, CmdbService, relation.service_id)
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    return _call(relations.update_relation, session, relation_id, payload, _actor(principal, trace_id))


def _list(session: Session, model: type, lifecycle: str, query: str | None,
          parent_field: str | None = None, parent_id: str | None = None,
          principal: AuthPrincipal | None = None) -> list:
    try:
        items = catalog.list_items(session, model, lifecycle, query, parent_field, parent_id)
    except ValueError as exc:
        _raise_domain_error(exc)
    if principal is None:
        return items
    visibility = cmdb_access.for_principal(session, principal)
    return [item for item in items if cmdb_access.allows(item, visibility)]


def _get(
    session: Session, model: type, item_id: str,
    principal: AuthPrincipal | None = None,
):
    try:
        item = catalog.get_item(session, model, item_id)
    except ValueError as exc:
        _raise_domain_error(exc)
    if principal is not None:
        try:
            cmdb_access.ensure_visible(item, cmdb_access.for_principal(session, principal))
        except cmdb_access.CmdbAccessError as exc:
            _raise_access_error(exc)
    return item


def _call(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        _raise_domain_error(exc)


def _change(session: Session, model: type, item_id: str, payload: CatalogUpdate,
            entity_type: str, principal: AuthPrincipal, trace_id: str | None):
    item = _get(session, model, item_id, principal)
    try:
        cmdb_access.ensure_writable(session, item, cmdb_access.for_principal(session, principal))
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)
    _enforce_archive_permission(principal, item, payload.lifecycle_status)
    return _call(catalog.update_item, session, item, payload, entity_type, _actor(principal, trace_id))


def _require_parent(
    session: Session, principal: AuthPrincipal, model: type, item_id: str
) -> object:
    return _get(session, model, item_id, principal)


def _require_writable_parent(
    session: Session, principal: AuthPrincipal, model: type, item_id: str
) -> None:
    item = _get(session, model, item_id, principal)
    try:
        cmdb_access.ensure_writable(session, item, cmdb_access.for_principal(session, principal))
    except cmdb_access.CmdbAccessError as exc:
        _raise_access_error(exc)


def _business_dict(item: CmdbBusiness) -> dict[str, object]:
    return BusinessResponse.model_validate(item).model_dump()


def _application_dict(item: CmdbApplication) -> dict[str, object]:
    return ApplicationResponse.model_validate(item).model_dump()


def _service_dict(session: Session, item: CmdbService) -> dict[str, object]:
    business_id, _ = catalog.ancestor_ids(session, item)
    return {**item.__dict__, "business_id": business_id}


def _api_dict(session: Session, item: CmdbServiceApi) -> dict[str, object]:
    business_id, application_id = catalog.ancestor_ids(session, item)
    return {**item.__dict__, "business_id": business_id, "application_id": application_id}


def _matches_tree(item: dict[str, object], query: str) -> bool:
    needle = query.strip().casefold()
    if needle in str(item.get("code", "")).casefold() or needle in str(item.get("name", "")).casefold():
        return True
    for key in ("applications", "services", "apis", "assets"):
        children = item.get(key, [])
        if isinstance(children, list) and any(_matches_tree(child, query) for child in children):
            return True
    return False
