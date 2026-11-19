import re
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.config import Settings
from src.models.auth import User
from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.services import kubernetes_helm_admission as admission
from src.services import kubernetes_helm_approvals as approvals
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_cluster_transport import (
    KubernetesConnectionError, RequestBudget, create_configmap, delete_configmap, read_configmap,
)
from src.services.kubernetes_clusters import _audit, _commit
from src.services.kubernetes_kubeconfig import KubernetesClusterError, ParsedKubeconfig, normalize_endpoint
from src.services.kubernetes_overview_jobs import db_now, utc

EXECUTOR_ACCOUNT = "linux-ops-helm-executor"
MAX_CONFIGMAPS = 8
_SLOTS = BoundedSemaphore(1)


class ExecutionConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    confirmation: str


def visible(
    row: KubernetesHelmApproval, cluster: KubernetesCluster, settings: Settings,
    principal: AuthPrincipal, session: Session,
) -> bool:
    if not (
        settings.kubernetes_helm_execution_enabled and settings.kubernetes_helm_execution_credentials_dir
        and settings.kubernetes_helm_execution_cluster_id == cluster.id
        and settings.kubernetes_helm_execution_namespace == row.namespace
        and re.fullmatch(r"helm-lab-[a-z0-9](?:[a-z0-9-]{0,52}[a-z0-9])?", row.namespace)
        and row.cluster_id == cluster.id and cluster.lifecycle == "active" and row.status == "approved"
        and row.reviewed_at is not None and row.reviewer_id and row.reviewer_id != row.requester_id
        and principal.user_id in {row.requester_id, row.reviewer_id}
        and row.cluster_revision == cluster.revision and row.credential_revision == cluster.credential_revision
        and 0 < len(row.resources) <= MAX_CONFIGMAPS
        and all(item["kind"] == "ConfigMap" and item["api_version"] == "v1" for item in row.resources)
    ):
        return False
    try:
        return normalize_endpoint(settings.kubernetes_helm_execution_endpoint) == cluster.endpoint and (
            KubernetesTargetPolicy.from_settings(settings).digest == row.policy_digest
            and utc(row.expires_at) > db_now(session)
        )
    except KubernetesClusterError:
        return False


def _read_credential(path: Path) -> str:
    try:
        if not path.is_file():
            raise ValueError
        value = path.read_text(encoding="ascii").strip()
        if not value or len(value.encode()) > 65_536:
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="专用执行凭据未配置或无法读取") from exc


def _identity(cluster: KubernetesCluster, settings: Settings) -> ParsedKubeconfig:
    path = Path(settings.kubernetes_helm_execution_credentials_dir)
    if not path.is_absolute():
        raise HTTPException(status_code=503, detail="专用执行凭据路径无效")
    token = _read_credential(path / "token")
    ca = _read_credential(path / "ca.crt")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{1,16384}", token):
        raise HTTPException(status_code=503, detail="专用执行令牌格式无效")
    return ParsedKubeconfig(cluster.endpoint, "helm-execution", "token", "", ca, token, None, None, None, None)


def _approval(
    session: Session, cluster_id: str, approval_id: str, settings: Settings, principal: AuthPrincipal,
) -> tuple[KubernetesCluster, KubernetesHelmApproval]:
    cluster = approvals.cluster_for_update(session, cluster_id)
    row = session.get(KubernetesHelmApproval, approval_id, populate_existing=True)
    if row is None or row.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if not visible(row, cluster, settings, principal, session):
        raise HTTPException(status_code=409, detail="该审批不符合隔离执行条件")
    for user_id in sorted((row.requester_id, row.reviewer_id)):
        user = session.get(User, user_id, with_for_update=True, populate_existing=True)
        if not user or not user.enabled or user.role != "admin" or user.must_change_password:
            raise HTTPException(status_code=409, detail="审批参与人权限已变化")
    return cluster, row


def _resource_name(payload: dict[str, object]) -> str:
    metadata = payload.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    if not isinstance(name, str):
        raise HTTPException(status_code=409, detail="审批资源名称无效")
    return name


def _validate_response(value: dict[str, object], namespace: str, name: str) -> None:
    metadata = value.get("metadata")
    if value.get("kind") != "ConfigMap" or not isinstance(metadata, dict) or (
        metadata.get("name") != name or metadata.get("namespace") != namespace
    ):
        raise KubernetesConnectionError("invalid_response", "集群返回的资源身份无效")


def execute(
    session: Session, cluster_id: str, approval_id: str, settings: Settings,
    principal: AuthPrincipal, trace_id: str,
) -> dict[str, object]:
    if not settings.kubernetes_helm_execution_enabled:
        raise HTTPException(status_code=403, detail="未启用 Helm 隔离执行")
    if not _SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="执行繁忙，请稍后再试")
    created: list[str] = []
    try:
        _, approved = _approval(session, cluster_id, approval_id, settings, principal)
        session.expunge(approved)
        session.rollback()
        objects = admission._resources(approved, settings)
        cluster, row = _approval(session, cluster_id, approval_id, settings, principal)
        if row.manifest_digest != approved.manifest_digest or row.source_digest != approved.source_digest:
            raise HTTPException(status_code=409, detail="审批内容已变化，请重新预览")
        identity = _identity(cluster, settings)
        budget = RequestBudget(deadline=monotonic() + 30, policy=KubernetesTargetPolicy.from_settings(settings))
        _audit(session, cluster_id, principal.username, "helm_execute", "started", trace_id, approval_id)
        _commit(session)
        code: str | None = None
        rollback = "not_needed"
        try:
            if admission.verify_identity(identity, budget) != f"system:serviceaccount:{row.namespace}:{EXECUTOR_ACCOUNT}":
                raise KubernetesConnectionError("identity_rejected", "执行身份不匹配")
            for payload in objects:
                name = _resource_name(payload)
                value = create_configmap(identity, row.namespace, payload, budget)
                created.append(name)
                _validate_response(value, row.namespace, name)
            for name in created:
                _validate_response(read_configmap(identity, row.namespace, name, budget), row.namespace, name)
            status = "succeeded"
        except KubernetesConnectionError as error:
            code = error.code
            if created:
                rollback = "succeeded"
                for name in reversed(created):
                    try:
                        delete_configmap(identity, row.namespace, name, budget)
                    except KubernetesConnectionError:
                        rollback = "failed"
                status = "rolled_back" if rollback == "succeeded" else "rollback_failed"
            else:
                status = "failed"
        _audit(session, cluster_id, principal.username, "helm_execute", status, trace_id,
               f"{approval_id}:{code or 'ok'}:{rollback}")
        _commit(session)
        return {
            "trace_id": trace_id, "status": status, "execution_available": True,
            "created": len(created) if status == "succeeded" else 0,
            "validated": len(created) if status == "succeeded" else 0,
            "rollback": rollback, "error_code": code,
        }
    finally:
        _SLOTS.release()
