import re
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_admin
from src.database import get_session
from src.models.auth import AuthSession
from src.models.kubernetes_cluster import KubernetesCluster, KubernetesClusterAudit
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.models.kubernetes_resource_snapshot import KubernetesResourceSnapshot
from src.schemas.kubernetes_cluster import (
    KubernetesClusterCreate, KubernetesClusterPatch, KubernetesClusterResponse,
    KubernetesClusterTest, KubernetesCredentialRotate,
)
from src.services import kubernetes_clusters as service
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services import kubernetes_overview, kubernetes_overview_jobs
from src.services.kubernetes_overview_collect import OBJECT_KINDS
from src.services.auth import hash_token


class SafeKubernetesRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(status_code=422, detail="集群请求格式无效") from exc

        return safe_handler


router = APIRouter(prefix="/kubernetes", tags=["kubernetes"], route_class=SafeKubernetesRoute)
Schema = TypeVar("Schema", bound=BaseModel)
RESOURCE_KINDS = frozenset(OBJECT_KINDS)
RESOURCE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def require_real_admin(
    response: Response, principal: AuthPrincipal = Depends(require_admin),
) -> AuthPrincipal:
    response.headers["Cache-Control"] = "no-store"
    if principal.role != "admin" or principal.auth_type != "session" or principal.user_id is None:
        raise HTTPException(status_code=403, detail="仅管理员登录会话可管理 Kubernetes 集群")
    return principal


def _validate(schema: type[Schema], body: object) -> Schema:
    try:
        return schema.model_validate(body)
    except ValidationError as exc:
        # Credentials must never appear in Pydantic validation input/context.
        raise HTTPException(status_code=422, detail="集群参数无效，请检查名称、配置和修订号") from exc


def _cluster(session: Session, cluster_id: str) -> KubernetesCluster:
    value = session.get(KubernetesCluster, cluster_id)
    if value is None:
        raise HTTPException(status_code=404, detail="集群不存在")
    return value


def _error(session: Session, exc: service.KubernetesClusterError) -> HTTPException:
    session.rollback()
    return HTTPException(
        status_code=409 if isinstance(exc, service.KubernetesClusterConflict) else 400,
        detail=str(exc),
    )


@router.get("/clusters")
def list_clusters(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, pattern="^(active|disabled|archived)$"),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    result = service.list_clusters(session, offset, limit, search, status)
    trace_id = str(uuid4())
    for item in result["items"]:
        service._audit(session, item["id"], principal.username, "listed", "accepted", trace_id)
    service._commit(session)
    return result


@router.get("/clusters/{cluster_id}", response_model=KubernetesClusterResponse)
def get_cluster(
    cluster_id: str, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    service._audit(session, cluster.id, principal.username, "viewed", "accepted", str(uuid4()))
    service._commit(session)
    return service.serialize(cluster)


@router.post("/clusters", response_model=KubernetesClusterResponse, status_code=201)
def create_cluster(
    request: Request, body: object = Body(...), session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesClusterCreate, body)
    try:
        cluster = service.create_cluster(session, payload, request.app.state.credential_vault,
                                         principal.username, str(uuid4()),
                                         KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings))
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    return service.serialize(cluster)


@router.post("/cluster-tests")
def test_unsaved_cluster(
    request: Request, body: object = Body(...), session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesClusterTest, body)
    if payload.kubeconfig is None or payload.context is None:
        raise HTTPException(status_code=422, detail="请提供 kubeconfig 和 context")
    try:
        parsed = service.parse_kubeconfig(payload.kubeconfig, payload.context, payload.endpoint)
        result = service.connection_test(
            parsed, KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings),
        )
        test_id = (service.register_test(session, parsed, result, principal.username)
                   if result["status"] == "passed" else None)
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    return {"test_id": test_id, "endpoint": parsed.endpoint, "context": parsed.context, **result}


@router.post("/clusters/{cluster_id}/tests")
def test_cluster(
    cluster_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesClusterTest, body)
    if payload.kubeconfig or payload.context or payload.endpoint:
        raise HTTPException(status_code=422, detail="已接入集群测试只使用已保存配置")
    try:
        return service.run_saved_test(
            session, _cluster(session, cluster_id), request.app.state.credential_vault,
            principal.username, payload.reason, str(uuid4()),
            KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings),
        )
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc


@router.patch("/clusters/{cluster_id}", response_model=KubernetesClusterResponse)
def patch_cluster(
    cluster_id: str, body: object = Body(...), session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesClusterPatch, body)
    try:
        updated = service.patch_cluster(session, _cluster(session, cluster_id), payload,
                                         principal.username, str(uuid4()))
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    return service.serialize(updated)


@router.put("/clusters/{cluster_id}/credential", response_model=KubernetesClusterResponse)
def rotate_credential(
    cluster_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesCredentialRotate, body)
    try:
        updated = service.rotate_credential(
            session, _cluster(session, cluster_id), payload, request.app.state.credential_vault,
            principal.username, str(uuid4()),
            KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings),
        )
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    return service.serialize(updated)


@router.get("/clusters/{cluster_id}/audits")
def get_audits(
    cluster_id: str, session: Session = Depends(get_session),
    _principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    _cluster(session, cluster_id)
    rows = session.query(KubernetesClusterAudit).filter(
        KubernetesClusterAudit.cluster_id == cluster_id,
    ).order_by(KubernetesClusterAudit.created_at.desc()).limit(100).all()
    return {"items": [
        {"id": item.id, "actor": item.actor, "action": item.action, "outcome": item.outcome,
         "trace_id": item.trace_id, "created_at": item.created_at}
        for item in rows
    ]}


@router.get("/clusters/{cluster_id}/overview")
def get_overview(
    cluster_id: str, request: Request, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    try:
        policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    result = kubernetes_overview.overview(session, cluster, policy)
    service._audit(session, cluster_id, principal.username, "overview_viewed", "accepted", str(uuid4()))
    service._commit(session)
    return result


def _latest_resource_run(
    session: Session, cluster: KubernetesCluster, policy: KubernetesTargetPolicy,
) -> KubernetesCollectionRun | None:
    rows = session.scalars(select(KubernetesCollectionRun).where(
        KubernetesCollectionRun.cluster_id == cluster.id,
        KubernetesCollectionRun.state.in_(("complete", "partial")),
    ).order_by(KubernetesCollectionRun.finished_at.desc(), KubernetesCollectionRun.id.desc()).limit(100)).all()
    for run in rows:
        if kubernetes_overview_jobs.current_run(session, cluster, run, policy) and (
            kubernetes_overview_jobs._requester_valid(session, run, kubernetes_overview_jobs.db_now(session))
        ):
            return run
    return None


def _resource_matches(item: dict[str, object], search: str | None, status: str | None) -> bool:
    if search and search.casefold() not in " ".join(
        str(item.get(key) or "") for key in ("name", "namespace", "uid", "node_name")
    ).casefold():
        return False
    if not status:
        return True
    value = status.casefold()
    values = {
        str(item.get(key)).casefold()
        for key in ("phase", "reason", "service_type", "ready", "kubelet_version")
        if item.get(key) is not None
    }
    if item.get("ready") is True:
        values.add("ready")
    if item.get("ready") is False:
        values.add("notready")
    return value in values


@router.get("/clusters/{cluster_id}/resources/{kind}")
def list_resources(
    cluster_id: str,
    kind: str,
    request: Request,
    namespace: str | None = Query(default=None, max_length=253),
    search: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=64),
    offset: int = Query(default=0, ge=0, le=10_000),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    if kind not in RESOURCE_KINDS:
        raise HTTPException(status_code=404, detail="资源类型不支持")
    if namespace is not None and not RESOURCE_NAME.fullmatch(namespace):
        raise HTTPException(status_code=422, detail="命名空间格式无效")
    cluster = _cluster(session, cluster_id)
    policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
    run = _latest_resource_run(session, cluster, policy)
    if run is None:
        return {
            "cluster_id": cluster_id, "kind": kind, "run_id": None, "state": "unobserved",
            "partial": False, "total": None, "items": [],
        }
    statement = select(KubernetesResourceSnapshot).where(
        KubernetesResourceSnapshot.cluster_id == cluster_id,
        KubernetesResourceSnapshot.run_id == run.id,
        KubernetesResourceSnapshot.kind == kind,
    ).order_by(KubernetesResourceSnapshot.namespace, KubernetesResourceSnapshot.name)
    all_rows = session.scalars(statement.limit(2000)).all()
    rows = [
        row for row in all_rows
        if namespace is None or row.namespace == namespace
    ]
    items = [
        dict(row.projection) for row in rows
        if _resource_matches(row.projection, search, status)
    ]
    summary = run.projection.get("resources", {}).get(kind, {}) if isinstance(run.projection, dict) else {}
    exact = (
        run.state == "complete"
        and isinstance(summary, dict)
        and summary.get("observed_count") == len(all_rows)
    )
    page = items[offset:offset + limit]
    service._audit(session, cluster_id, principal.username, "resource_listed", "accepted", str(uuid4()))
    service._commit(session)
    return {
        "cluster_id": cluster_id, "kind": kind, "run_id": run.id, "state": run.state,
        "partial": run.state == "partial" or not exact, "total": len(items) if exact else None,
        "items": page,
    }


@router.get("/clusters/{cluster_id}/resources/{kind}/{uid}")
def get_resource(
    cluster_id: str,
    kind: str,
    uid: str,
    request: Request,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    if kind not in RESOURCE_KINDS or not 1 <= len(uid) <= 256:
        raise HTTPException(status_code=404, detail="资源不存在")
    cluster = _cluster(session, cluster_id)
    policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
    run = _latest_resource_run(session, cluster, policy)
    if run is None:
        raise HTTPException(status_code=404, detail="资源快照不存在")
    row = session.scalar(select(KubernetesResourceSnapshot).where(
        KubernetesResourceSnapshot.cluster_id == cluster_id,
        KubernetesResourceSnapshot.run_id == run.id,
        KubernetesResourceSnapshot.kind == kind,
        KubernetesResourceSnapshot.uid == uid,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    service._audit(session, cluster_id, principal.username, "resource_viewed", "accepted", str(uuid4()))
    service._commit(session)
    return {
        "cluster_id": cluster_id, "kind": kind, "run_id": run.id, "state": run.state,
        "item": dict(row.projection),
    }


@router.post("/clusters/{cluster_id}/refresh", status_code=202)
def refresh_overview(
    cluster_id: str, request: Request, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    _cluster(session, cluster_id)
    raw = request.cookies.get(request.app.state.runtime_settings.auth_cookie_name, "")
    auth = session.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
    try:
        run, merged = kubernetes_overview_jobs.enqueue(
            session, cluster_id, KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings),
            principal.user_id, auth.id if auth else None,
        )
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    return {"cluster_id": cluster_id, "run_id": run.id, "state": run.state, "merged": merged}


@router.get("/clusters/{cluster_id}/collections/{run_id}")
def get_collection_run(
    cluster_id: str, run_id: str, request: Request, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    run = session.get(KubernetesCollectionRun, run_id)
    if run is None or run.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="采集记录不存在")
    try:
        policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
    except service.KubernetesClusterError as exc:
        raise _error(session, exc) from exc
    current = (
        kubernetes_overview_jobs.current_run(session, cluster, run, policy)
        and kubernetes_overview_jobs._requester_valid(
            session, run, kubernetes_overview_jobs.db_now(session),
        )
    )
    result = kubernetes_overview.serialize_run(
        run, None if current else "cancelled", None if current else "collection_revoked",
    )
    service._audit(session, cluster_id, principal.username, "collection_viewed", "accepted", str(uuid4()))
    service._commit(session)
    return result
