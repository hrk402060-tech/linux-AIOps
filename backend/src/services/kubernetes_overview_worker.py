import asyncio
from datetime import timedelta
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.models.kubernetes_cluster import KubernetesCluster
from src.models.kubernetes_collection import KubernetesCollectionRun
from src.services.crypto import CredentialVault
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_clusters import _decrypt_config
from src.services.kubernetes_kubeconfig import KubernetesClusterError
from src.services.kubernetes_overview_collect import collect_overview
from src.services.kubernetes_overview_jobs import (
    CollectionRevoked, cancel_claim, check_claim, claim_next, db_now, enqueue, finalize, utc,
)
from src.services.kubernetes_overview_transport import CollectionBudget

logger = logging.getLogger(__name__)


def enqueue_due(factory: sessionmaker[Session], settings: Settings) -> None:
    policy = KubernetesTargetPolicy.from_settings(settings)
    if not policy.allowed_networks:
        return
    with factory() as session:
        rows = session.scalars(select(KubernetesCluster).where(
            KubernetesCluster.lifecycle == "active", KubernetesCluster.collection_interval_seconds > 0,
        )).all()
        for cluster in rows:
            recent = session.scalar(select(func.max(KubernetesCollectionRun.requested_at)).where(
                KubernetesCollectionRun.cluster_id == cluster.id,
            ))
            if recent is None or utc(recent) + timedelta(seconds=cluster.collection_interval_seconds) <= db_now(session):
                try:
                    enqueue(session, cluster.id, policy)
                except KubernetesClusterError:
                    session.rollback()
                    logger.info("Kubernetes periodic collection was rejected")


def process_next(factory: sessionmaker[Session], vault: CredentialVault, settings: Settings) -> bool:
    claim = claim_next(factory)
    if claim is None:
        return False
    def policy() -> KubernetesTargetPolicy:
        return KubernetesTargetPolicy.from_settings(settings)
    try:
        check_claim(factory, claim, policy())
        with factory() as session:
            run = session.get(KubernetesCollectionRun, claim[0])
            cluster = session.get(KubernetesCluster, run.cluster_id)
            parsed = _decrypt_config(vault, cluster)
        budget = CollectionBudget(policy=policy(), guard=lambda: check_claim(factory, claim, policy()))
        projection, state = collect_overview(parsed, budget)
        if not finalize(factory, claim, policy(), projection, state):
            cancel_claim(factory, claim)
    except CollectionRevoked:
        cancel_claim(factory, claim)
    except KubernetesClusterError:
        if not finalize(factory, claim, policy(), None, "failed", "credential_unavailable"):
            cancel_claim(factory, claim)
    except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError):
        # Exception text may contain upstream data; log only a fixed diagnostic.
        logger.error("Kubernetes overview collection failed")
        if not finalize(factory, claim, policy(), None, "failed", "collection_failed"):
            cancel_claim(factory, claim)
    return True


async def run_scheduler(
    factory: sessionmaker[Session], vault: CredentialVault, settings: Settings, stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(enqueue_due, factory, settings)
            await asyncio.to_thread(process_next, factory, vault, settings)
        except (SQLAlchemyError, KubernetesClusterError):
            logger.error("Kubernetes overview scheduler failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except asyncio.TimeoutError:
            continue
