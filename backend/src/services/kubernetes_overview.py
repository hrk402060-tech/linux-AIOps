from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_overview_collect import KindResult, RESOURCE_KINDS
from src.services.kubernetes_overview_jobs import current_run, db_now, utc, _requester_valid
from src.services.kubernetes_overview_projection import project_resources, safe_error


def overview(session: Session, cluster: KubernetesCluster, policy: KubernetesTargetPolicy) -> dict[str, object]:
    empty = project_resources({kind: KindResult("unobserved", [], None) for kind in RESOURCE_KINDS})
    rows = session.scalars(select(KubernetesCollectionRun).where(
        KubernetesCollectionRun.cluster_id == cluster.id,
        KubernetesCollectionRun.cluster_revision == cluster.revision,
        KubernetesCollectionRun.credential_revision == cluster.credential_revision,
        KubernetesCollectionRun.policy_digest == policy.digest,
    ).order_by(KubernetesCollectionRun.requested_at.desc(), KubernetesCollectionRun.id.desc()).limit(100)).all()
    rows = [run for run in rows if current_run(session, cluster, run, policy)
            and _requester_valid(session, run, db_now(session))]
    latest = rows[0] if rows else None
    snapshot = next((run for run in rows if run.state in {"complete", "partial"} and run.projection), None)
    success = next((run for run in rows if run.state == "complete" and run.projection), None)
    projection = {**empty, **snapshot.projection} if snapshot else empty
    observed_at = projection.get("observed_at")
    freshness = "unobserved" if latest is None else "unavailable"
    if snapshot and snapshot.finished_at:
        age = db_now(session) - utc(snapshot.finished_at)
        threshold = timedelta(seconds=max(180, cluster.collection_interval_seconds * 3))
        freshness = "fresh" if age <= threshold and latest.state not in {"failed", "cancelled"} else "stale"
    if cluster.lifecycle != "active":
        freshness = "unavailable"
    errors = list(projection.get("errors", []))
    if latest and latest.error_code:
        errors.append(safe_error("collection", latest.error_code))
    return {
        **projection, "cluster_id": cluster.id,
        "collection_state": latest.state if latest else "unobserved", "freshness": freshness,
        "run_id": latest.id if latest else None, "observed_at": observed_at,
        "last_success_at": success.projection.get("observed_at") if success else None, "errors": errors,
    }


def serialize_run(
    run: KubernetesCollectionRun, state: str | None = None, error_code: str | None = None,
) -> dict[str, object]:
    return {
        "cluster_id": run.cluster_id, "run_id": run.id, "state": state or run.state,
        "trace_id": run.trace_id,
        "requested_at": run.requested_at, "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error_code": error_code if error_code is not None else run.error_code,
    }
