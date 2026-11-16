"""Short-lived, single-use test receipts shared by all API workers."""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.kubernetes_cluster import KubernetesConnectionTest
from src.services.kubernetes_kubeconfig import KubernetesClusterError, ParsedKubeconfig

TEST_TTL_SECONDS = 300
logger = logging.getLogger(__name__)


def _digest(parsed: ParsedKubeconfig, policy_digest: str) -> str:
    content = "\0".join((parsed.endpoint, parsed.context, parsed.secret_json(), policy_digest))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def register_test(
    session: Session, parsed: ParsedKubeconfig, result: dict[str, object], actor: str,
) -> str:
    if result.get("status") != "passed":
        raise KubernetesClusterError("连接测试未通过，请修正后重试")
    policy_digest = result.get("target_policy_digest")
    if not isinstance(policy_digest, str) or len(policy_digest) != 64:
        raise KubernetesClusterError("连接测试已失效或配置已变化，请重新测试")
    now = datetime.now(timezone.utc)
    session.execute(delete(KubernetesConnectionTest).where(KubernetesConnectionTest.expires_at <= now))
    safe_result = json.loads(json.dumps(result, default=lambda value: value.isoformat()))
    safe_result["tested_at"] = now.isoformat()
    test_id = str(uuid4())
    session.add(KubernetesConnectionTest(
        id=test_id, actor=actor, config_digest=_digest(parsed, policy_digest), result=safe_result,
        expires_at=now + timedelta(seconds=TEST_TTL_SECONDS),
    ))
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.error("Kubernetes connection test receipt could not be saved")
        raise
    return test_id


def consume_test(
    session: Session, test_id: str, parsed: ParsedKubeconfig, actor: str, policy_digest: str,
) -> dict[str, object]:
    # DELETE...RETURNING consumes atomically and rolls back with the cluster write.
    result = session.execute(delete(KubernetesConnectionTest).where(
        KubernetesConnectionTest.id == test_id,
        KubernetesConnectionTest.actor == actor,
        KubernetesConnectionTest.config_digest == _digest(parsed, policy_digest),
        KubernetesConnectionTest.expires_at > datetime.now(timezone.utc),
    ).returning(KubernetesConnectionTest.result)).scalar_one_or_none()
    if result is None:
        raise KubernetesClusterError("连接测试已失效或配置已变化，请重新测试")
    return result
