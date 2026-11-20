from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.models.auth import AuthSession, User
from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.models.kubernetes_resource_snapshot import KubernetesResourceSnapshot
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_clusters import _audit, _commit
from src.services.kubernetes_kubeconfig import KubernetesClusterError

logger = logging.getLogger(__name__)
Claim = tuple[str, str]
ACTIVE = ("queued", "running")


class CollectionRevoked(RuntimeError):
    """A fixed cancellation signal; never carries upstream data."""


def utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def db_now(session: Session) -> datetime:
    clock = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else func.current_timestamp()
    value = session.scalar(select(clock))
    if value is None:
        return datetime.now(timezone.utc)
    return utc(value)


def _requester_valid(
    session: Session, run: KubernetesCollectionRun, now: datetime, locked: bool = False,
) -> bool:
    if run.source == "periodic":
        return run.requested_by is None and run.requester_session_id is None
    user_statement = select(User).where(User.id == run.requested_by)
    auth_statement = select(AuthSession).where(AuthSession.id == run.requester_session_id)
    user = session.scalar(user_statement.with_for_update() if locked else user_statement)
    auth = session.scalar(auth_statement.with_for_update() if locked else auth_statement)
    now = db_now(session) if locked else now
    return bool(
        user and user.enabled and user.role == "admin" and not user.must_change_password
        and user.access_revision == run.requester_revision
        and auth and auth.user_id == user.id and auth.revoked_at is None
        and utc(auth.expires_at) > now
    )


def current_run(
    session: Session, cluster: KubernetesCluster, run: KubernetesCollectionRun,
    policy: KubernetesTargetPolicy,
) -> bool:
    return bool(
        cluster.lifecycle == "active" and cluster.credential is not None
        and cluster.revision == run.cluster_revision
        and cluster.credential_revision == run.credential_revision
        and cluster.credential.credential_revision == run.credential_revision
        and policy.digest == run.policy_digest and policy.allowed_networks
        and (run.source != "periodic" or cluster.collection_interval_seconds > 0)
    )


def enqueue(
    session: Session, cluster_id: str, policy: KubernetesTargetPolicy,
    user_id: str | None = None, session_id: str | None = None,
) -> tuple[KubernetesCollectionRun, bool]:
    policy.require_configured()
    cluster = session.scalar(select(KubernetesCluster).where(
        KubernetesCluster.id == cluster_id,
    ).with_for_update().execution_options(populate_existing=True))
    if cluster is None or cluster.lifecycle != "active" or cluster.credential is None:
        raise KubernetesClusterError("集群不可用，无法刷新总览")
    if user_id is None and cluster.collection_interval_seconds <= 0:
        raise KubernetesClusterError("集群自动采集已关闭")
    existing = session.scalar(select(KubernetesCollectionRun).where(
        KubernetesCollectionRun.cluster_id == cluster_id, KubernetesCollectionRun.state.in_(ACTIVE),
    ))
    if existing and current_run(session, cluster, existing, policy) and _requester_valid(session, existing, db_now(session)):
        return existing, True
    if existing:
        existing.state, existing.error_code = "cancelled", "collection_revoked"
        existing.finished_at = db_now(session)
        session.flush()
    user = session.get(User, user_id) if user_id else None
    run = KubernetesCollectionRun(
        id=str(uuid4()), cluster_id=cluster_id, state="queued",
        source="manual" if user_id else "periodic", trace_id=str(uuid4()), requested_by=user_id,
        requester_revision=user.access_revision if user else None, requester_session_id=session_id,
        cluster_revision=cluster.revision, credential_revision=cluster.credential_revision,
        policy_digest=policy.digest, requested_at=db_now(session),
    )
    if not _requester_valid(session, run, db_now(session), locked=True):
        raise KubernetesClusterError("管理员会话已失效，请重新登录")
    session.add(run)
    _audit(session, cluster_id, user.username if user else "system", "overview_queued", "accepted", run.id)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        merged = session.scalar(select(KubernetesCollectionRun).where(
            KubernetesCollectionRun.cluster_id == cluster_id, KubernetesCollectionRun.state.in_(ACTIVE),
        ))
        current_cluster = session.get(KubernetesCluster, cluster_id)
        if (
            merged is None or current_cluster is None
            or not current_run(session, current_cluster, merged, policy)
            or not _requester_valid(session, merged, db_now(session))
        ):
            raise KubernetesClusterError("采集排队冲突，请重新刷新") from None
        return merged, True
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Kubernetes collection enqueue failed")
        raise
    return run, False


def claim_next(factory: sessionmaker[Session]) -> Claim | None:
    with factory() as session:
        try:
            now = db_now(session)
            available = or_(KubernetesCollectionRun.state == "queued", and_(
                KubernetesCollectionRun.state == "running",
                KubernetesCollectionRun.lease_expires_at <= now,
            ))
            run_id = session.scalar(select(KubernetesCollectionRun.id).where(available).order_by(
                KubernetesCollectionRun.requested_at, KubernetesCollectionRun.id,
            ).limit(1))
            if run_id is None:
                return None
            token = str(uuid4())
            result = session.execute(update(KubernetesCollectionRun).where(
                KubernetesCollectionRun.id == run_id, available,
            ).values(state="running", lease_token=token, lease_expires_at=now + timedelta(seconds=120),
                     started_at=now, finished_at=None, error_code=None))
            session.commit()
            return (run_id, token) if result.rowcount == 1 else None
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Kubernetes collection claim failed")
            raise


def _validate_claim(
    session: Session, claim: Claim, policy: KubernetesTargetPolicy, locked: bool = False,
) -> KubernetesCollectionRun:
    statement = select(KubernetesCollectionRun).where(KubernetesCollectionRun.id == claim[0])
    run = session.scalar(statement)
    if (
        run is None or run.state != "running" or run.lease_token != claim[1]
        or run.lease_expires_at is None
    ):
        raise CollectionRevoked("collection_revoked")
    statement = select(KubernetesCluster).where(KubernetesCluster.id == run.cluster_id)
    cluster = session.scalar(statement.with_for_update() if locked else statement)
    if locked:
        run = session.scalar(select(KubernetesCollectionRun).where(
            KubernetesCollectionRun.id == claim[0],
        ).with_for_update().execution_options(populate_existing=True))
    now = db_now(session)
    if (
        run.state != "running" or run.lease_token != claim[1] or run.lease_expires_at is None
        or utc(run.lease_expires_at) <= now or cluster is None
        or not current_run(session, cluster, run, policy)
        or not _requester_valid(session, run, now, locked=locked)
    ):
        raise CollectionRevoked("collection_revoked")
    return run


def check_claim(
    factory: sessionmaker[Session], claim: Claim, policy: KubernetesTargetPolicy,
) -> None:
    with factory() as session:
        _validate_claim(session, claim, policy)


def finalize(
    factory: sessionmaker[Session], claim: Claim, policy: KubernetesTargetPolicy,
    projection: dict[str, object] | None, state: str, error_code: str | None = None,
) -> bool:
    with factory() as session:
        try:
            run = _validate_claim(session, claim, policy, locked=True)
            now = db_now(session)
            snapshot_items = {}
            if isinstance(projection, dict):
                snapshot_items = projection.pop("resource_items", {})
            if isinstance(snapshot_items, dict) and state in {"complete", "partial"}:
                session.execute(delete(KubernetesResourceSnapshot).where(
                    KubernetesResourceSnapshot.run_id == run.id,
                ))
                for kind, items in snapshot_items.items():
                    if not isinstance(kind, str) or not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        uid, name = item.get("uid"), item.get("name")
                        if not isinstance(uid, str) or not isinstance(name, str):
                            continue
                        namespace = item.get("namespace")
                        namespace = namespace if isinstance(namespace, str) else None
                        resource_version = item.get("resource_version")
                        resource_version = resource_version if isinstance(resource_version, str) else None
                        session.add(KubernetesResourceSnapshot(
                            id=str(uuid4()), run_id=run.id, cluster_id=run.cluster_id,
                            kind=kind, namespace=namespace, uid=uid, name=name,
                            resource_version=resource_version, observed_at=now,
                            projection=item,
                        ))
            result = session.execute(update(KubernetesCollectionRun).where(
                KubernetesCollectionRun.id == claim[0], KubernetesCollectionRun.state == "running",
                KubernetesCollectionRun.lease_token == claim[1], KubernetesCollectionRun.lease_expires_at > now,
            ).values(state=state, projection=projection, error_code=error_code,
                     finished_at=now, lease_expires_at=None).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                return False
            _audit(session, run.cluster_id, "system", "overview_collected", state, run.id)
            _commit(session)
            return True
        except CollectionRevoked:
            session.rollback()
            return False


def cancel_claim(factory: sessionmaker[Session], claim: Claim) -> None:
    with factory() as session:
        try:
            session.execute(update(KubernetesCollectionRun).where(
                KubernetesCollectionRun.id == claim[0], KubernetesCollectionRun.lease_token == claim[1],
                KubernetesCollectionRun.state == "running",
            ).values(state="cancelled", error_code="collection_revoked",
                     finished_at=db_now(session), lease_expires_at=None, projection=None))
            _commit(session)
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Kubernetes collection cancellation failed")
            raise
