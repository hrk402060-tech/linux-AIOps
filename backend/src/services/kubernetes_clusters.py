from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from src.models.cmdb import CmdbService
from src.models.kubernetes_cluster import (
    KubernetesCluster, KubernetesClusterAudit, KubernetesClusterCredential,
)
from src.schemas.kubernetes_cluster import (
    KubernetesClusterCreate, KubernetesClusterPatch, KubernetesCredentialRotate,
)
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.kubernetes_cluster_connection import connection_test
from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy
from src.services.kubernetes_cluster_tests import consume_test, register_test  # noqa: F401
from src.services.kubernetes_kubeconfig import (
    KubernetesClusterError, ParsedKubeconfig, parse_kubeconfig,
)

logger = logging.getLogger(__name__)
SECRET_TYPE = "kubernetes_cluster_config"


class KubernetesClusterConflict(KubernetesClusterError):
    pass


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value and not value.tzinfo else value


def _date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _aad(cluster_id: str, revision: int) -> str:
    return f"{SECRET_TYPE}:{cluster_id}:{revision}"


def _encrypt_config(
    vault: CredentialVault, cluster_id: str, revision: int, parsed: ParsedKubeconfig,
) -> tuple[bytes, bytes]:
    return vault.encrypt(parsed.secret_json(), _aad(cluster_id, revision), SECRET_TYPE)


def _decrypt_config(vault: CredentialVault, cluster: KubernetesCluster) -> ParsedKubeconfig:
    credential = cluster.credential
    if credential is None:
        raise KubernetesClusterError("集群凭据不可用，请轮换凭据")
    try:
        raw = vault.decrypt(credential.encrypted_config, credential.nonce,
                            _aad(cluster.id, credential.credential_revision),
                            SECRET_TYPE, credential.key_version)
        values = json.loads(raw)
        return ParsedKubeconfig(
            cluster.endpoint, cluster.context, credential.auth_type, credential.identity_hint or "",
            values.get("ca_pem"), values.get("token"), values.get("client_cert_pem"),
            values.get("client_key_pem"), _utc(cluster.ca_cert_expires_at),
            _utc(cluster.client_cert_expires_at),
        )
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, AttributeError) as exc:
        raise KubernetesClusterError("保存的集群凭据无法读取，请轮换凭据") from exc


def _commit(session: Session) -> None:
    try:
        session.commit()
    except (StaleDataError, IntegrityError) as exc:
        session.rollback()
        raise KubernetesClusterConflict("集群已变化或名称已存在，请刷新后重试") from exc
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Kubernetes cluster transaction failed")
        raise


def _audit(
    session: Session, cluster_id: str, actor: str, action: str, outcome: str,
    trace_id: str | None, detail: str | None = None,
) -> None:
    session.add(KubernetesClusterAudit(
        cluster_id=cluster_id, actor=actor, action=action, outcome=outcome,
        trace_id=trace_id[:128] if trace_id else None, detail=detail[:500] if detail else None,
    ))


def serialize(cluster: KubernetesCluster) -> dict[str, object]:
    fields = ("id", "name", "endpoint", "context", "service_id", "lifecycle",
              "connection_status", "authorization_status", "version", "capabilities",
              "collection_interval_seconds", "revision", "credential_revision",
              "error_code", "error_message")
    dates = ("server_cert_expires_at", "ca_cert_expires_at", "client_cert_expires_at",
             "last_tested_at", "last_success_at", "updated_at")
    return {
        **{key: getattr(cluster, key) for key in fields},
        **{key: _utc(getattr(cluster, key)) for key in dates},
        "secret_configured": cluster.credential is not None,
        "identity_hint": cluster.credential.identity_hint if cluster.credential else None,
    }


def list_clusters(
    session: Session, offset: int, limit: int, search: str | None, status: str | None,
) -> dict[str, object]:
    statement = select(KubernetesCluster)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(or_(
            KubernetesCluster.name.ilike(pattern), KubernetesCluster.endpoint.ilike(pattern),
            KubernetesCluster.context.ilike(pattern),
        ))
    if status:
        statement = statement.where(KubernetesCluster.lifecycle == status)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(statement.order_by(
        KubernetesCluster.updated_at.desc(), KubernetesCluster.id.desc(),
    ).offset(offset).limit(limit)).all()
    return {"total": total, "items": [serialize(item) for item in rows]}


def _check_service(session: Session, service_id: str | None) -> None:
    if service_id and session.get(CmdbService, service_id) is None:
        raise KubernetesClusterError("关联业务服务不存在，请选择有效服务")


def _apply_result(cluster: KubernetesCluster, result: dict[str, object]) -> None:
    passed = result["status"] == "passed"
    cluster.last_tested_at = _date(result.get("tested_at")) or datetime.now(timezone.utc)
    cluster.connection_status = "connected" if passed else "failed"
    cluster.authorization_status = "verified" if passed else "unknown"
    cluster.version = result.get("version")
    cluster.capabilities = result.get("capabilities")
    cluster.server_cert_expires_at = _date(result.get("server_cert_expires_at"))
    cluster.error_code = result.get("error_code")
    cluster.error_message = result.get("error_message")
    if cluster.credential and isinstance(result.get("identity_hint"), str):
        cluster.credential.identity_hint = result["identity_hint"]
    if passed:
        cluster.last_success_at = cluster.last_tested_at


def _set_credential(
    cluster: KubernetesCluster, parsed: ParsedKubeconfig, vault: CredentialVault, revision: int,
) -> None:
    encrypted, nonce = _encrypt_config(vault, cluster.id, revision, parsed)
    credential = cluster.credential or KubernetesClusterCredential(cluster_id=cluster.id)
    credential.auth_type = parsed.auth_type
    credential.encrypted_config, credential.nonce = encrypted, nonce
    credential.key_version, credential.credential_revision = vault.key_version, revision
    credential.identity_hint = parsed.identity_hint
    credential.rotated_at = datetime.now(timezone.utc)
    cluster.credential = credential
    cluster.credential_revision = revision
    cluster.endpoint, cluster.context = parsed.endpoint, parsed.context
    cluster.ca_cert_expires_at = parsed.ca_expires_at
    cluster.client_cert_expires_at = parsed.client_cert_expires_at


def create_cluster(
    session: Session, payload: KubernetesClusterCreate, vault: CredentialVault,
    actor: str, trace_id: str | None, policy: KubernetesTargetPolicy,
) -> KubernetesCluster:
    _check_service(session, payload.service_id)
    parsed = parse_kubeconfig(payload.kubeconfig, payload.context, payload.endpoint)
    policy.require_configured()
    result = consume_test(session, payload.test_id, parsed, actor, policy.digest)
    cluster = KubernetesCluster(id=str(uuid4()), name=payload.name, service_id=payload.service_id,
                                lifecycle="active", revision=1, collection_interval_seconds=0)
    _set_credential(cluster, parsed, vault, 1)
    _apply_result(cluster, result)
    session.add(cluster)
    _audit(session, cluster.id, actor, "created", "accepted", trace_id)
    _commit(session)
    session.refresh(cluster)
    return cluster


def _check_revision(cluster: KubernetesCluster, revision: int) -> None:
    if cluster.revision != revision:
        raise KubernetesClusterConflict("集群已变化，请刷新后重试")


def patch_cluster(
    session: Session, cluster: KubernetesCluster, payload: KubernetesClusterPatch,
    actor: str, trace_id: str | None,
) -> KubernetesCluster:
    _check_revision(cluster, payload.expected_revision)
    fields = payload.model_fields_set
    if "service_id" in fields:
        _check_service(session, payload.service_id)
        cluster.service_id = payload.service_id
    if payload.name is not None:
        cluster.name = payload.name
    if payload.lifecycle:
        cluster.lifecycle = payload.lifecycle
    cluster.revision += 1
    _audit(session, cluster.id, actor, "updated", "accepted", trace_id, payload.reason)
    _commit(session)
    session.refresh(cluster)
    return cluster


def rotate_credential(
    session: Session, cluster: KubernetesCluster, payload: KubernetesCredentialRotate,
    vault: CredentialVault, actor: str, trace_id: str | None, policy: KubernetesTargetPolicy,
) -> KubernetesCluster:
    _check_revision(cluster, payload.expected_revision)
    if cluster.lifecycle == "archived":
        raise KubernetesClusterError("已归档集群不能轮换凭据")
    parsed = parse_kubeconfig(payload.kubeconfig, payload.context, payload.endpoint)
    policy.require_configured()
    result = consume_test(session, payload.test_id, parsed, actor, policy.digest)
    _set_credential(cluster, parsed, vault, cluster.credential_revision + 1)
    _apply_result(cluster, result)
    cluster.revision += 1
    _audit(session, cluster.id, actor, "credential_rotated", "accepted", trace_id, payload.reason)
    _commit(session)
    session.refresh(cluster)
    return cluster


def run_saved_test(
    session: Session, cluster: KubernetesCluster, vault: CredentialVault, actor: str,
    reason: str, trace_id: str | None, policy: KubernetesTargetPolicy,
) -> dict[str, object]:
    if cluster.lifecycle != "active":
        raise KubernetesClusterError("集群已停用，启用后可测试连接")
    result = connection_test(_decrypt_config(vault, cluster), policy)
    # version_id_col prevents an old test overwriting a concurrent rotation/disable.
    _apply_result(cluster, result)
    cluster.revision += 1
    _audit(session, cluster.id, actor, "tested", str(result["status"]), trace_id, reason)
    _commit(session)
    return {"cluster": serialize(cluster), **result}
