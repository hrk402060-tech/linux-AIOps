from datetime import timedelta

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.config import Settings
from src.models.auth import User
from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_helm_approval import KubernetesHelmApproval
from src.services import kubernetes_clusters
from src.services.kubernetes_change_preflight import Helm, build_preflight
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_helm import HelmPreview, HelmPreviewError, verified_source
from src.services.kubernetes_overview_jobs import db_now, utc

PREVIEW_TTL = timedelta(minutes=10)
REVIEW_TTL = timedelta(minutes=30)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    preview_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=2, max_length=240)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    confirmation: str = Field(pattern=r"^(yes|no)$")

    @field_validator("confirmation")
    @classmethod
    def validate_confirmation(cls, value: str) -> str:
        if value not in {"yes", "no"}:
            raise ValueError("Invalid confirmation")
        return value


def cluster_for_update(session: Session, cluster_id: str) -> KubernetesCluster:
    cluster = session.scalar(select(KubernetesCluster).where(KubernetesCluster.id == cluster_id)
                             .with_for_update().execution_options(populate_existing=True))
    if cluster is None:
        raise HTTPException(status_code=404, detail="集群不存在")
    return cluster


def save_preview(
    session: Session, cluster: KubernetesCluster, change: Helm, rendered: HelmPreview,
    policy: KubernetesTargetPolicy, principal: AuthPrincipal, trace_id: str,
) -> KubernetesHelmApproval:
    assert principal.user_id is not None
    now = db_now(session)
    row = KubernetesHelmApproval(
        id=trace_id, cluster_id=cluster.id, cluster_revision=cluster.revision,
        credential_revision=cluster.credential_revision, policy_digest=policy.digest,
        source_digest=rendered.source_digest, manifest_digest=rendered.manifest_digest,
        chart=rendered.chart.model_dump(exclude={"chart_path", "manifest"}),
        resources=[item.model_dump() for item in rendered.resources],
        namespace=change.namespace, release_name=change.release_name, status="previewed",
        requester_id=principal.user_id, requested_by=principal.username, created_at=now, expires_at=now + PREVIEW_TTL,
    )
    session.add(row)
    return row


def serialize(row: KubernetesHelmApproval, cluster: KubernetesCluster, session: Session,
              principal: AuthPrincipal) -> dict[str, object]:
    status = row.status
    if status == "pending" and utc(row.expires_at) <= db_now(session):
        status = "expired"
    elif status == "pending" and (row.cluster_revision != cluster.revision or cluster.lifecycle != "active"):
        status = "invalidated"
    return {
        "id": row.id, "status": status, "cluster_revision": row.cluster_revision,
        "chart": row.chart, "resources": row.resources, "namespace": row.namespace, "release_name": row.release_name,
        "requested_by": row.requested_by, "reviewed_by": row.reviewed_by, "reason": row.reason,
        "submitted_at": utc(row.submitted_at) if row.submitted_at else None,
        "reviewed_at": utc(row.reviewed_at) if row.reviewed_at else None,
        "expires_at": utc(row.expires_at), "execution_available": False,
        "can_review": status == "pending" and row.requester_id != principal.user_id,
    }


def _validate_current(
    session: Session, cluster: KubernetesCluster, row: KubernetesHelmApproval, settings: Settings,
) -> None:
    policy = KubernetesTargetPolicy.from_settings(settings)
    if (cluster.revision != row.cluster_revision or cluster.credential_revision != row.credential_revision
            or policy.digest != row.policy_digest or cluster.lifecycle != "active"):
        raise HTTPException(status_code=409, detail="集群配置或目标策略已变化，请重新预览")
    try:
        _, _, source_digest = verified_source(
            settings.kubernetes_helm_trusted_charts, row.chart["id"], row.chart["digest"],
        )
    except HelmPreviewError as exc:
        raise HTTPException(status_code=409, detail="受信 Chart 已变化或不可用，请重新预览") from exc
    if source_digest != row.source_digest:
        raise HTTPException(status_code=409, detail="受信 Chart 已变化，请重新预览")
    change = Helm(
        action="helm", expected_revision=row.cluster_revision, namespace=row.namespace,
        release_name=row.release_name, chart_id=row.chart["id"], chart_digest=row.chart["digest"],
    )
    if build_preflight(session, cluster, policy, change, row.id, settings).status == "blocked":
        raise HTTPException(status_code=409, detail="集群预检已失效，请刷新快照后重新预览")


def transition(
    session: Session, cluster_id: str, approval_id: str, settings: Settings, principal: AuthPrincipal,
    payload: ApprovalRequest | ApprovalDecision,
) -> dict[str, object]:
    cluster = cluster_for_update(session, cluster_id)
    row = session.get(KubernetesHelmApproval, approval_id)
    if row is None or row.cluster_id != cluster_id:
        raise HTTPException(status_code=404, detail="预览或审批记录不存在")
    submitting = isinstance(payload, ApprovalRequest)
    previous = "previewed" if submitting else "pending"
    if row.status != previous or utc(row.expires_at) <= db_now(session):
        raise HTTPException(status_code=409, detail="预览或审批已处理或过期")
    if submitting != (row.requester_id == principal.user_id):
        raise HTTPException(status_code=409, detail="仅发起人可提交，审批需由另一位管理员确认")
    _validate_current(session, cluster, row, settings)
    if not submitting:
        requester = session.get(User, row.requester_id)
        if requester is None or not requester.enabled or requester.role != "admin":
            raise HTTPException(status_code=409, detail="发起人不再具备管理员权限")
    now = db_now(session)
    status = "pending" if submitting else ("approved" if payload.confirmation == "yes" else "rejected")
    changes = (
        {"reason": payload.reason, "submitted_at": now, "expires_at": now + REVIEW_TTL} if submitting
        else {"reviewer_id": principal.user_id, "reviewed_by": principal.username, "reviewed_at": now}
    )
    changed = session.execute(update(KubernetesHelmApproval).where(
        KubernetesHelmApproval.id == row.id, KubernetesHelmApproval.status == previous,
        KubernetesHelmApproval.expires_at > now,
    ).values(status=status, **changes).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(status_code=409, detail="预览或审批已处理或过期")
    kubernetes_clusters._audit(session, cluster.id, principal.username, "helm_approval", status, row.id, row.chart["id"])
    session.refresh(row)
    result = serialize(row, cluster, session, principal)
    kubernetes_clusters._commit(session)
    return result
