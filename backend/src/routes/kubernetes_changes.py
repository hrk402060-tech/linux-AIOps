from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.database import get_session
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.routes.kubernetes import SafeKubernetesRoute, _cluster, _error, _validate, require_real_admin
from src.services import kubernetes_clusters, kubernetes_helm_approvals as approvals
from src.services import kubernetes_helm_admission as admission
from src.services.kubernetes_change_preflight import CHANGE_INPUT, Helm, Preflight, build_preflight
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_helm import HelmPreviewError, preview, public_catalog
from src.services.kubernetes_overview_jobs import utc

router = APIRouter(prefix="/kubernetes", tags=["kubernetes"], route_class=SafeKubernetesRoute)


@router.get("/helm-charts")
def helm_charts(request: Request, principal: AuthPrincipal = Depends(require_real_admin)) -> dict[str, object]:
    del principal
    return public_catalog(request.app.state.runtime_settings.kubernetes_helm_trusted_charts)


@router.post("/clusters/{cluster_id}/helm-preview")
def helm_preview(
    cluster_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    try:
        change = Helm.model_validate({**body, "action": "helm"} if isinstance(body, dict) else None)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Helm 预览参数无效") from error
    cluster = _cluster(session, cluster_id)
    if cluster.revision != change.expected_revision:
        raise HTTPException(status_code=409, detail="集群配置已变化，请刷新后重试")
    try:
        policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
        rendered = preview(
            request.app.state.runtime_settings.kubernetes_helm_trusted_charts, change.chart_id,
            change.chart_digest, change.release_name, change.namespace,
            request.app.state.runtime_settings.kubernetes_helm_binary,
        )
        cluster = approvals.cluster_for_update(session, cluster_id)
        if cluster.revision != change.expected_revision:
            raise HTTPException(status_code=409, detail="集群配置已变化，请刷新后重试")
        preflight_result = build_preflight(session, cluster, policy, change, str(uuid4()))
        row = (approvals.save_preview(session, cluster, change, rendered, policy, principal, preflight_result.trace_id)
               if preflight_result.status != "blocked" else None)
        preview_expires_at = utc(row.expires_at) if row else None
        kubernetes_clusters._audit(session, cluster.id, principal.username, "helm_preview", preflight_result.status,
                                   preflight_result.trace_id, f"{rendered.chart.id}:{len(rendered.resources)}")
        kubernetes_clusters._commit(session)
    except HelmPreviewError as error:
        session.rollback()
        raise HTTPException(status_code=503 if "未配置" in str(error) else 422, detail=str(error)) from error
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error
    return {
        "preflight": preflight_result,
        "chart": rendered.chart.model_dump(exclude={"chart_path", "manifest"}),
        "release_name": change.release_name, "namespace": change.namespace,
        "resources": rendered.resources, "preview_id": preflight_result.trace_id if row else None,
        "preview_expires_at": preview_expires_at,
    }


@router.get("/clusters/{cluster_id}/helm-approvals")
def helm_approvals(
    cluster_id: str, request: Request, offset: int = Query(0, ge=0),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    query = select(KubernetesHelmApproval).where(
        KubernetesHelmApproval.cluster_id == cluster_id, KubernetesHelmApproval.submitted_at.is_not(None),
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(
        KubernetesHelmApproval.submitted_at.desc(), KubernetesHelmApproval.id.desc(),
    ).offset(offset).limit(10)).all()
    settings = request.app.state.runtime_settings
    return {"items": [
        {**approvals.serialize(row, cluster, session, principal),
         "can_rehearse": admission.visible(row, cluster, settings, principal, session)}
        for row in rows
    ], "total": total}


@router.post("/clusters/{cluster_id}/helm-approvals", status_code=201)
def submit_helm_approval(
    cluster_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(approvals.ApprovalRequest, body)
    try:
        return approvals.transition(session, cluster_id, payload.preview_id, request.app.state.runtime_settings, principal, payload)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error


@router.post("/clusters/{cluster_id}/helm-approvals/{approval_id}/decision")
def decide_helm_approval(
    cluster_id: str, approval_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(approvals.ApprovalDecision, body)
    try:
        return approvals.transition(session, cluster_id, approval_id, request.app.state.runtime_settings, principal, payload)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error


@router.post("/clusters/{cluster_id}/helm-approvals/{approval_id}/admission-rehearsal")
def rehearse_helm_admission(
    cluster_id: str, approval_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(admission.RehearsalConfirmation, body)
    if payload.confirmation != "yes":
        raise HTTPException(status_code=422, detail="请输入 yes 确认演练")
    try:
        return admission.rehearse(
            session, cluster_id, approval_id, request.app.state.runtime_settings, principal, str(uuid4()),
        )
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error


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
