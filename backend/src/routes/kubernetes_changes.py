from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.database import get_session
from src.routes.kubernetes import SafeKubernetesRoute, _cluster, _error, require_real_admin
from src.services import kubernetes_clusters
from src.services.kubernetes_change_preflight import CHANGE_INPUT, Preflight, build_preflight
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy

router = APIRouter(prefix="/kubernetes", tags=["kubernetes"], route_class=SafeKubernetesRoute)


@router.post("/clusters/{cluster_id}/change-preflight", response_model=Preflight)
def preflight(
    cluster_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> Preflight:
    try:
        change = CHANGE_INPUT.validate_python(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="变更参数无效") from error
    cluster = _cluster(session, cluster_id)
    if cluster.revision != change.expected_revision:
        raise HTTPException(status_code=409, detail="集群配置已变化，请刷新后重试")
    try:
        policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
        result = build_preflight(session, cluster, policy, change, str(uuid4()))
        kubernetes_clusters._audit(
            session, cluster.id, principal.username, "change_preflight", result.status,
            result.trace_id, f"{change.action}:{result.status}",
        )
        kubernetes_clusters._commit(session)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error
    return result
