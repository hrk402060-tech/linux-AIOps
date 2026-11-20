from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.database import get_session
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.models.kubernetes_backup import KubernetesBackupArtifact
from src.schemas.kubernetes_backup import KubernetesBackupRegister, KubernetesBackupRestoreTarget
from src.routes.kubernetes import SafeKubernetesRoute, _cluster, _error, _validate, require_real_admin
from src.services import kubernetes_clusters, kubernetes_helm_approvals as approvals
from src.services import kubernetes_helm_admission as admission
from src.services import kubernetes_helm_execution as execution
from src.services.kubernetes_change_preflight import (
    CHANGE_INPUT, ChangeCapabilities, Helm, Preflight, build_preflight, change_capabilities,
)
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_helm import HelmPreviewError, preview, public_catalog
from src.services import kubernetes_backups as backups
from src.services.kubernetes_overview_jobs import utc

router = APIRouter(prefix="/kubernetes", tags=["kubernetes"], route_class=SafeKubernetesRoute)


@router.get("/clusters/{cluster_id}/change-capabilities", response_model=ChangeCapabilities)
def capabilities(
    cluster_id: str, request: Request,
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> ChangeCapabilities:
    cluster = _cluster(session, cluster_id)
    policy = KubernetesTargetPolicy.from_settings(request.app.state.runtime_settings)
    result = change_capabilities(
        session, cluster, policy,
        bool(public_catalog(request.app.state.runtime_settings.kubernetes_helm_trusted_charts)["enabled"]),
    )
    kubernetes_clusters._audit(session, cluster.id, principal.username, "change_capabilities_viewed",
                               "accepted", str(uuid4()))
    kubernetes_clusters._commit(session)
    return result


@router.get("/helm-charts")
def helm_charts(request: Request, principal: AuthPrincipal = Depends(require_real_admin)) -> dict[str, object]:
    del principal
    return public_catalog(request.app.state.runtime_settings.kubernetes_helm_trusted_charts)


@router.get("/clusters/{cluster_id}/backups")
def list_backups(
    cluster_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    result = backups.list_artifacts(session, cluster.id, offset, limit)
    kubernetes_clusters._audit(session, cluster.id, principal.username, "backup_listed", "accepted", str(uuid4()))
    kubernetes_clusters._commit(session)
    return result


@router.post("/clusters/{cluster_id}/backups", status_code=201)
def register_backup(
    cluster_id: str, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesBackupRegister, body)
    try:
        artifact = backups.register(session, _cluster(session, cluster_id), payload, principal.username)
        kubernetes_clusters._audit(
            session, cluster_id, principal.username, "backup_registered", "accepted",
            str(uuid4()), artifact.id,
        )
        kubernetes_clusters._commit(session)
        return backups.serialize(artifact)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error


@router.post("/clusters/{cluster_id}/backups/{backup_id}/verify")
def verify_backup(
    cluster_id: str, backup_id: str, request: Request,
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    cluster = _cluster(session, cluster_id)
    artifact = session.get(KubernetesBackupArtifact, backup_id)
    if artifact is None or artifact.cluster_id != cluster.id:
        raise HTTPException(status_code=404, detail="备份产物不存在")
    result = backups.verify(session, artifact, request.app.state.runtime_settings)
    kubernetes_clusters._audit(
        session, cluster.id, principal.username, "backup_verified", result.status,
        str(uuid4()), artifact.id,
    )
    kubernetes_clusters._commit(session)
    return backups.serialize(result)


@router.put("/clusters/{cluster_id}/backups/{backup_id}/restore-target")
def set_restore_target(
    cluster_id: str, backup_id: str, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(KubernetesBackupRestoreTarget, body)
    cluster = _cluster(session, cluster_id)
    artifact = session.get(KubernetesBackupArtifact, backup_id)
    if artifact is None or artifact.cluster_id != cluster.id:
        raise HTTPException(status_code=404, detail="备份产物不存在")
    try:
        artifact = backups.set_restore_target(session, artifact, cluster, payload)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error
    kubernetes_clusters._audit(
        session, cluster.id, principal.username, "restore_target_registered", "accepted",
        str(uuid4()), f"{artifact.id}:{payload.target_key}",
    )
    kubernetes_clusters._commit(session)
    return backups.serialize(artifact)


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
        preflight_result = build_preflight(
            session, cluster, policy, change, str(uuid4()), request.app.state.runtime_settings,
        )
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
         "can_rehearse": admission.visible(row, cluster, settings, principal, session),
         "can_execute": execution.visible(row, cluster, settings, principal, session)}
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


@router.post("/clusters/{cluster_id}/helm-approvals/{approval_id}/execute")
def execute_helm_approval(
    cluster_id: str, approval_id: str, request: Request, body: object = Body(...),
    session: Session = Depends(get_session), principal: AuthPrincipal = Depends(require_real_admin),
) -> dict[str, object]:
    payload = _validate(execution.ExecutionConfirmation, body)
    if payload.confirmation != "yes":
        raise HTTPException(status_code=422, detail="请输入 yes 确认执行")
    try:
        return execution.execute(
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
        result = build_preflight(
            session, cluster, policy, change, str(uuid4()), request.app.state.runtime_settings,
        )
        kubernetes_clusters._audit(
            session, cluster.id, principal.username, "change_preflight", result.status,
            result.trace_id, f"{change.action}:{result.status}",
        )
        kubernetes_clusters._commit(session)
    except kubernetes_clusters.KubernetesClusterError as error:
        raise _error(session, error) from error
    return result
