import hashlib
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
from src.services import kubernetes_helm_approvals as approvals
from src.services.kubernetes_cluster_authorization import verify_identity
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_cluster_transport import KubernetesConnectionError, RequestBudget, dry_run_configmap
from src.services.kubernetes_clusters import _audit, _commit
from src.services.kubernetes_helm import HelmPreviewError, load_documents, render_chart, summarize, verified_source
from src.services.kubernetes_kubeconfig import KubernetesClusterError, ParsedKubeconfig, normalize_endpoint
from src.services.kubernetes_overview_jobs import db_now, utc

SERVICE_ACCOUNT = "linux-ops-helm-rehearsal"
MAX_CONFIGMAPS = 8
MAX_CREDENTIAL_BYTES = 65_536
_SLOTS = BoundedSemaphore(2)


class RehearsalConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    confirmation: str


def visible(
    row: KubernetesHelmApproval, cluster: KubernetesCluster, settings: Settings,
    principal: AuthPrincipal, session: Session,
) -> bool:
    if not (
        settings.kubernetes_helm_rehearsal_enabled and settings.kubernetes_helm_rehearsal_credentials_dir
        and settings.kubernetes_helm_rehearsal_cluster_id == cluster.id
        and settings.kubernetes_helm_rehearsal_namespace == row.namespace
        and re.fullmatch(r"helm-lab-[a-z0-9](?:[a-z0-9-]{0,52}[a-z0-9])?", row.namespace)
        and row.cluster_id == cluster.id and cluster.lifecycle == "active"
        and row.status == "approved" and row.reviewed_at is not None
        and row.reviewer_id and row.reviewer_id != row.requester_id
        and principal.user_id in {row.requester_id, row.reviewer_id}
        and row.cluster_revision == cluster.revision and row.credential_revision == cluster.credential_revision
        and 0 < len(row.resources) <= MAX_CONFIGMAPS
        and all(item["kind"] == "ConfigMap" and item["api_version"] == "v1" for item in row.resources)
    ):
        return False
    try:
        return (
            normalize_endpoint(settings.kubernetes_helm_rehearsal_endpoint) == cluster.endpoint
            and KubernetesTargetPolicy.from_settings(settings).digest == row.policy_digest
            and utc(row.expires_at) > db_now(session)
        )
    except KubernetesClusterError:
        return False


def _approval(
    session: Session, cluster_id: str, approval_id: str, settings: Settings, principal: AuthPrincipal,
) -> tuple[KubernetesCluster, KubernetesHelmApproval]:
    cluster = approvals.cluster_for_update(session, cluster_id)
    row = session.get(KubernetesHelmApproval, approval_id, populate_existing=True)
    if row is None or row.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if not visible(row, cluster, settings, principal, session):
        raise HTTPException(status_code=409, detail="该审批不符合隔离演练条件")
    for user_id in sorted((row.requester_id, row.reviewer_id)):
        user = session.get(User, user_id, with_for_update=True, populate_existing=True)
        if not user or not user.enabled or user.role != "admin" or user.must_change_password:
            raise HTTPException(status_code=409, detail="审批参与人权限已变化")
    return cluster, row


def _resources(row: KubernetesHelmApproval, settings: Settings) -> list[dict[str, object]]:
    try:
        chart, data, source_digest = verified_source(
            settings.kubernetes_helm_trusted_charts, row.chart["id"], row.chart["digest"],
        )
        if source_digest != row.source_digest:
            raise HelmPreviewError
        manifest = render_chart(chart, data, row.release_name, row.namespace, settings.kubernetes_helm_binary)
        if hashlib.sha256(manifest).hexdigest() != row.manifest_digest:
            raise HelmPreviewError
        if [item.model_dump() for item in summarize(manifest, row.namespace)] != row.resources:
            raise HelmPreviewError
        return [_configmap(item) for item in load_documents(manifest) if item is not None]
    except HelmPreviewError as exc:
        raise HTTPException(status_code=409, detail="Chart 内容与审批不符或不支持隔离演练") from exc


def _configmap(item: object) -> dict[str, object]:
    if (
        not isinstance(item, dict) or item.get("apiVersion") != "v1" or item.get("kind") != "ConfigMap"
        or set(item) - {"apiVersion", "kind", "metadata", "data"}
    ):
        raise HelmPreviewError
    metadata = item.get("metadata")
    data = item.get("data", {})
    if (
        not isinstance(metadata, dict) or set(metadata) - {"name", "namespace", "labels"}
        or not _string_map(data) or not _string_map(metadata.get("labels", {}))
    ):
        raise HelmPreviewError
    return item


def _string_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def _read_credential(path: Path) -> str:
    if not path.is_file():
        raise ValueError
    with path.open("rb") as stream:
        value = stream.read(MAX_CREDENTIAL_BYTES + 1)
    if not value or len(value) > MAX_CREDENTIAL_BYTES:
        raise ValueError
    return value.decode("ascii").strip()


def _identity(cluster: KubernetesCluster, settings: Settings) -> ParsedKubeconfig:
    path = Path(settings.kubernetes_helm_rehearsal_credentials_dir)
    try:
        if not path.is_absolute():
            raise ValueError
        token = _read_credential(path / "token")
        ca = _read_credential(path / "ca.crt")
        if not ca or not re.fullmatch(r"[A-Za-z0-9._~-]{1,16384}", token):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="专用演练凭据未配置或无法读取") from exc
    return ParsedKubeconfig(cluster.endpoint, "helm-rehearsal", "token", "", ca, token, None, None, None, None)


def verify_rehearsal_identity(parsed: ParsedKubeconfig, namespace: str, budget: RequestBudget) -> None:
    if verify_identity(parsed, budget) != f"system:serviceaccount:{namespace}:{SERVICE_ACCOUNT}":
        raise KubernetesConnectionError("identity_rejected", "演练身份不匹配")


def _run(
    session: Session, cluster_id: str, approval_id: str, settings: Settings, principal: AuthPrincipal, trace_id: str,
) -> dict[str, object]:
    _, approved = _approval(session, cluster_id, approval_id, settings, principal)
    # Rendering reads trusted local files only and must not hold database locks.
    session.expunge(approved)
    session.rollback()
    objects = _resources(approved, settings)
    cluster, row = _approval(session, cluster_id, approval_id, settings, principal)
    if row.manifest_digest != approved.manifest_digest or row.source_digest != approved.source_digest:
        raise HTTPException(status_code=409, detail="审批内容已变化，请重新预览")
    identity = _identity(cluster, settings)
    namespace = row.namespace
    budget = RequestBudget(deadline=monotonic() + 10, policy=KubernetesTargetPolicy.from_settings(settings))
    _audit(session, cluster_id, principal.username, "helm_admission_rehearsal", "started", trace_id, approval_id)
    _commit(session)
    checked, code = 0, None
    try:
        verify_rehearsal_identity(identity, namespace, budget)
        for payload in objects:
            dry_run_configmap(identity, namespace, payload, budget)
            checked += 1
    except KubernetesConnectionError as exc:
        code = exc.code
    status = "passed" if code is None else "failed"
    _audit(session, cluster_id, principal.username, "helm_admission_rehearsal", status,
           trace_id, f"{approval_id}:{code or 'ok'}")
    _commit(session)
    return {"status": status, "checked": checked, "total": len(objects), "error_code": code,
            "trace_id": trace_id, "execution_available": False}


def rehearse(
    session: Session, cluster_id: str, approval_id: str, settings: Settings, principal: AuthPrincipal, trace_id: str,
) -> dict[str, object]:
    if not settings.kubernetes_helm_rehearsal_enabled:
        raise HTTPException(status_code=403, detail="未启用 Helm 隔离演练")
    if not _SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="演练繁忙，请稍后再试")
    try:
        return _run(session, cluster_id, approval_id, settings, principal, trace_id)
    finally:
        _SLOTS.release()
