from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import ipaddress
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.database import Base
from src.models.auth import AuthSession, User
from src.models.kubernetes_cluster import KubernetesCluster, KubernetesClusterCredential
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.services import kubernetes_overview_jobs as jobs
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_kubeconfig import KubernetesClusterError

pytest_plugins = ("test_kubernetes_cluster_postgresql",)


def seed(engine: Engine) -> tuple[sessionmaker[Session], KubernetesTargetPolicy]:
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        user = User(id="overview-admin", username="overview-admin", role="admin",
                    password_hash="unused", enabled=True, access_revision=0)
        session.add(user)
        session.flush()
        session.add(AuthSession(
            id="overview-session", user_id=user.id, token_hash="f" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        cluster = KubernetesCluster(
            id="overview-cluster", name="overview", endpoint="https://192.0.2.20:6443",
            context="selected", lifecycle="active", revision=1, credential_revision=1,
            collection_interval_seconds=60,
        )
        cluster.credential = KubernetesClusterCredential(
            id="overview-credential", cluster_id=cluster.id, auth_type="token",
            encrypted_config=b"synthetic-encrypted", nonce=b"n" * 12,
            key_version="test", credential_revision=1,
        )
        session.add(cluster)
        session.commit()
    return factory, KubernetesTargetPolicy((ipaddress.ip_network("192.0.2.20/32"),), ())


def test_postgresql_concurrent_refresh_merges_and_single_worker_claims(
    isolated_pg: tuple[Engine, str],
) -> None:
    engine, _ = isolated_pg
    factory, policy = seed(engine)
    barrier = Barrier(2)
    def refresh() -> tuple[str, bool]:
        with factory() as session:
            barrier.wait(timeout=10)
            run, merged = jobs.enqueue(session, "overview-cluster", policy, "overview-admin", "overview-session")
            return run.id, merged
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(refresh) for _ in range(2)]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0][0] == ids[1][0]
    assert sorted(item[1] for item in ids) == [False, True]
    barrier = Barrier(2)
    def claim() -> jobs.Claim | None:
        barrier.wait(timeout=10)
        return jobs.claim_next(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=15) for future in futures]
    assert sum(claim is not None for claim in claims) == 1
    with factory() as session:
        assert len(session.scalars(select(KubernetesCollectionRun)).all()) == 1


def test_postgresql_expired_worker_and_disabled_cluster_cannot_publish(
    isolated_pg: tuple[Engine, str],
) -> None:
    engine, _ = isolated_pg
    factory, policy = seed(engine)
    with factory() as session:
        jobs.enqueue(session, "overview-cluster", policy, "overview-admin", "overview-session")
    first = jobs.claim_next(factory)
    assert first is not None
    with factory() as session:
        run = session.get(KubernetesCollectionRun, first[0])
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    second = jobs.claim_next(factory)
    assert second is not None and second[1] != first[1]
    assert jobs.finalize(factory, first, policy, {"counts": {"nodes": 111}}, "complete") is False
    assert jobs.finalize(factory, second, policy, {"counts": {"nodes": 2}}, "complete") is True
    with factory() as session:
        jobs.enqueue(session, "overview-cluster", policy, "overview-admin", "overview-session")
    disabled_claim = jobs.claim_next(factory)
    assert disabled_claim is not None
    with factory() as session:
        cluster = session.get(KubernetesCluster, "overview-cluster")
        cluster.lifecycle, cluster.revision = "disabled", cluster.revision + 1
        session.commit()
    assert jobs.finalize(factory, disabled_claim, policy, {"counts": {"nodes": 222}}, "complete") is False
    with factory() as session:
        assert session.get(KubernetesCollectionRun, disabled_claim[0]).projection is None


def test_postgresql_credential_rotation_revokes_inflight_finalize(
    isolated_pg: tuple[Engine, str],
) -> None:
    engine, _ = isolated_pg
    factory, policy = seed(engine)
    with factory() as session:
        jobs.enqueue(session, "overview-cluster", policy, "overview-admin", "overview-session")
    claim = jobs.claim_next(factory)
    assert claim is not None
    with factory() as session:
        cluster = session.get(KubernetesCluster, "overview-cluster")
        cluster.revision += 1
        cluster.credential_revision += 1
        cluster.credential.credential_revision = cluster.credential_revision
        session.commit()
    assert jobs.finalize(factory, claim, policy, {"counts": {"nodes": 333}}, "complete") is False
    with factory() as session:
        assert session.get(KubernetesCollectionRun, claim[0]).projection is None


def test_postgresql_periodic_selection_cannot_queue_after_interval_is_disabled(
    isolated_pg: tuple[Engine, str],
) -> None:
    engine, _ = isolated_pg
    factory, policy = seed(engine)
    with factory() as stale_selector:
        due = stale_selector.get(KubernetesCluster, "overview-cluster")
        assert due.collection_interval_seconds == 60
        with factory() as writer:
            cluster = writer.get(KubernetesCluster, due.id)
            cluster.collection_interval_seconds, cluster.revision = 0, cluster.revision + 1
            writer.commit()
        with pytest.raises(KubernetesClusterError):
            jobs.enqueue(stale_selector, due.id, policy)
    with factory() as session:
        assert session.scalar(select(KubernetesCollectionRun)) is None
