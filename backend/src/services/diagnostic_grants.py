from datetime import datetime, timezone
import base64
import hashlib
import logging
import ipaddress
from uuid import uuid4

import paramiko

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.credential import Credential
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit
from src.schemas.diagnostic_grant import DiagnosticConfirmation, DiagnosticGrantInput
from src.services.crypto import CredentialVault
from src.services.readonly_diagnostics import require_action, ReadOnlyPolicyError
from src.services.scan_engine import _parse_private_key
from src.services.terminal_assistant import redact_context

logger = logging.getLogger(__name__)
SECRET_TYPE = 'diagnostic_private_key'


class DiagnosticAuthorizationError(PermissionError):
    pass


class DiagnosticGrantConflict(ValueError):
    pass


class DiagnosticGrantInvalid(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def effective_status(session: Session, grant: DiagnosticGrant, asset: Asset) -> str:
    if not grant.enabled:
        return 'disabled'
    if _utc(grant.expires_at) <= datetime.now(timezone.utc):
        return 'expired'
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        return 'asset_disabled'
    if grant.kind == 'kubernetes':
        from src.services import kubernetes_grants
        return kubernetes_grants.effective_status(grant, asset)
    if grant.kind == 'docker':
        from src.services import docker_grants
        return docker_grants.effective_status(grant, asset)
    if grant.kind == 'mysql':
        from src.services import mysql_grants
        return mysql_grants.effective_status(grant, asset)
    if grant.kind == 'redis':
        from src.services import redis_grants
        return redis_grants.effective_status(grant, asset)
    if grant.kind == 'rabbitmq':
        from src.services import rabbitmq_grants
        return rabbitmq_grants.effective_status(grant, asset)
    if grant.kind != 'linux':
        return 'disabled'
    if (grant.host, grant.port) != (asset.host, asset.ssh_port):
        return 'target_changed'
    try:
        ipaddress.ip_address(grant.host)
    except ValueError:
        return 'target_changed'
    manual = session.get(Credential, asset.credential_id) if asset.credential_id else None
    if grant.username in {'root', 'admin', 'administrator'} or manual and manual.username == grant.username:
        return 'disabled'
    return 'active'


def require_grant(session: Session, asset: Asset, revision: int | None = None) -> DiagnosticGrant:
    # A fresh transaction observes revocation even after long-running remote I/O.
    with Session(session.get_bind()) as fresh:
        current = fresh.get(Asset, asset.id)
        grant = fresh.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
        if current is None or grant is None or effective_status(fresh, grant, current) != 'active':
            raise DiagnosticAuthorizationError('Dedicated diagnostic authorization unavailable')
        if revision is not None and revision != grant.revision:
            raise DiagnosticAuthorizationError('Diagnostic authorization changed')
        try:
            if not grant.action_ids:
                raise ReadOnlyPolicyError('Empty authorization')
            if grant.kind == 'kubernetes':
                from src.services.kubernetes_grants import require_scope
                require_scope(grant)
            elif grant.kind == 'docker':
                from src.services.docker_grants import require_scope
                require_scope(grant)
            elif grant.kind == 'mysql':
                from src.services.mysql_grants import require_scope
                require_scope(grant)
            elif grant.kind == 'redis':
                from src.services import redis_grants
                redis_grants.require_scope(grant)
            elif grant.kind == 'rabbitmq':
                from src.services import rabbitmq_grants
                rabbitmq_grants.require_scope(grant)
            elif grant.kind == 'linux':
                for action in grant.action_ids:
                    require_action(action, grant.action_ids)
            else:
                raise ReadOnlyPolicyError('Unknown authorization kind')
        except ReadOnlyPolicyError as exc:
            raise DiagnosticAuthorizationError('Diagnostic scope invalid') from exc
        fresh.expunge(grant)
        return grant


def serialize(session: Session, grant: DiagnosticGrant, asset: Asset) -> dict[str, object]:
    result = {
        'kind': grant.kind,
        'asset_id': asset.id, 'asset_name': asset.name, 'host': grant.host, 'port': grant.port,
        'username': grant.username, 'action_ids': grant.action_ids, 'expires_at': _utc(grant.expires_at),
        'enabled': grant.enabled, 'effective_status': effective_status(session, grant, asset),
        'revision': grant.revision, 'key_fingerprint': grant.key_fingerprint,
        'host_fingerprint': grant.host_fingerprint, 'secret_configured': True,
        'operator': grant.operator, 'updated_at': _utc(grant.updated_at),
    }
    if grant.kind == 'kubernetes':
        from src.services.kubernetes_grants import serialize_config
        result.update(serialize_config(grant))
    elif grant.kind == 'docker':
        from src.services.docker_grants import serialize_config
        result.update(serialize_config(grant))
    elif grant.kind == 'mysql':
        from src.services.mysql_grants import serialize_config
        result.update(serialize_config(grant))
    elif grant.kind == 'redis':
        from src.services.redis_grants import serialize_config
        result.update(serialize_config(grant))
    elif grant.kind == 'rabbitmq':
        from src.services.rabbitmq_grants import serialize_config
        result.update(serialize_config(grant))
    return result


def list_grants(
    session: Session, offset: int, limit: int, asset_id: str | None,
    visible_asset_ids: set[str] | None = None,
) -> dict[str, object]:
    from src.services.readonly_diagnostics import list_actions
    statement = select(DiagnosticGrant, Asset).join(Asset, Asset.id == DiagnosticGrant.asset_id)
    if asset_id:
        statement = statement.where(DiagnosticGrant.asset_id == asset_id)
    if visible_asset_ids is not None:
        statement = statement.where(DiagnosticGrant.asset_id.in_(visible_asset_ids))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(statement.order_by(DiagnosticGrant.updated_at.desc(), DiagnosticGrant.id.desc())
                           .offset(offset).limit(limit)).all()
    return {'total': total, 'items': [serialize(session, grant, asset) for grant, asset in rows],
            'catalog': [{**item, 'label': _ACTION_LABELS.get(str(item['id']), item['label'])}
                        for item in list_actions()]}


_ACTION_LABELS = {'kubernetes.pods': 'Pod 状态', 'kubernetes.workloads': '工作负载状态',
                  'kubernetes.events': '事件摘要', 'linux.uptime': '运行时间与负载', 'linux.memory': '内存与 Swap',
                  'linux.filesystem': '磁盘使用', 'linux.inodes': 'inode 使用',
                  'linux.failed_units': '失败服务', 'linux.processes': '进程资源',
                  'docker.containers': '容器状态', 'docker.resources': '容器资源',
                  'mysql.status': 'MySQL 状态', 'mysql.replication': 'MySQL 复制状态',
                  'redis.status': 'Redis 状态', 'redis.memory': 'Redis 内存',
                  'redis.clients': 'Redis 连接', 'redis.slowlog': 'Redis 慢日志摘要',
                  'rabbitmq.status': 'RabbitMQ 状态', 'rabbitmq.nodes': 'RabbitMQ 节点',
                  'rabbitmq.queues': 'RabbitMQ 队列摘要', 'rabbitmq.consumers': 'RabbitMQ 消费者摘要'}


def upsert(session: Session, payload: DiagnosticGrantInput, vault: CredentialVault,
           operator: str) -> DiagnosticGrant:
    asset = session.get(Asset, payload.asset_id)
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise DiagnosticGrantInvalid('请选择启用的服务器资产')
    _confirmation(asset, payload)
    item = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
    if item is not None and item.kind != 'linux':
        raise DiagnosticGrantConflict('已有授权类型不可转换，请选择独立服务器资产')
    try:
        ipaddress.ip_address(asset.host)
    except ValueError as exc:
        raise DiagnosticGrantInvalid('本阶段只读诊断仅支持 IP 地址资产') from exc
    manual = session.get(Credential, asset.credential_id) if asset.credential_id else None
    if manual is not None and manual.username == payload.username:
        raise DiagnosticGrantInvalid('诊断身份不能复用人工 SSH 身份')
    if (item.revision if item else 0) != payload.expected_revision:
        raise DiagnosticGrantConflict('授权已变化，请刷新后重试')
    if item is None and payload.private_key is None:
        raise DiagnosticGrantInvalid('首次授权必须提供专用私钥')
    identifier = item.id if item else str(uuid4())
    values = _values(payload, asset, identifier, vault, item)
    values['operator'] = operator
    try:
        if item is None:
            item = DiagnosticGrant(id=identifier, asset_id=asset.id, **values)
            session.add(item)
        else:
            count = session.execute(update(DiagnosticGrant).where(
                DiagnosticGrant.id == item.id, DiagnosticGrant.revision == payload.expected_revision,
            ).values(**values), execution_options={'synchronize_session': False}).rowcount
            if count != 1:
                raise DiagnosticGrantConflict('授权已变化，请刷新后重试')
        _audit(session, asset.id, 'created' if payload.expected_revision == 0 else 'reconfigured',
               operator, payload.reason, payload.expected_revision + 1)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DiagnosticGrantConflict('授权已变化，请刷新后重试') from exc
    except SQLAlchemyError:
        session.rollback()
        logger.exception('Diagnostic grant transaction failed')
        raise
    except DiagnosticGrantConflict:
        session.rollback()
        raise
    session.refresh(item)
    return item


def _values(payload: DiagnosticGrantInput, asset: Asset, identifier: str,
            vault: CredentialVault, previous: DiagnosticGrant | None) -> dict[str, object]:
    values: dict[str, object] = {
        'host': asset.host, 'port': asset.ssh_port, 'host_key_type': '',
        'host_public_key': '', 'host_fingerprint': '',
        'username': payload.username, 'action_ids': payload.action_ids, 'expires_at': payload.expires_at,
        'enabled': True, 'revision': payload.expected_revision + 1, 'updated_at': datetime.now(timezone.utc),
    }
    if payload.private_key is not None:
        try:
            key = _parse_private_key(payload.private_key)
        except (ValueError, paramiko.SSHException) as exc:
            # Key parser exceptions must never echo supplied key material.
            raise DiagnosticGrantInvalid('专用私钥格式无效或需要口令') from exc
        encrypted, nonce = vault.encrypt(payload.private_key, identifier, SECRET_TYPE)
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        values.update(encrypted_secret=encrypted, nonce=nonce, key_version=vault.key_version,
                      key_fingerprint=fingerprint)
    elif previous is not None and previous.username != payload.username:
        raise DiagnosticGrantInvalid('变更诊断身份必须提供新的专用私钥')
    return values


def revoke(session: Session, asset_id: str, payload: DiagnosticConfirmation,
           operator: str) -> DiagnosticGrant:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise DiagnosticGrantInvalid('服务器资产不存在')
    _confirmation(asset, payload)
    item = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset_id))
    if item is None or not item.enabled:
        raise DiagnosticGrantConflict('授权不存在或已经撤销')
    try:
        count = session.execute(update(DiagnosticGrant).where(
            DiagnosticGrant.id == item.id, DiagnosticGrant.revision == payload.expected_revision,
            DiagnosticGrant.enabled.is_(True),
        ).values(enabled=False, revision=payload.expected_revision + 1, operator=operator,
                 updated_at=datetime.now(timezone.utc)), execution_options={'synchronize_session': False}).rowcount
        if count != 1:
            raise DiagnosticGrantConflict('授权已变化，请刷新后重试')
        _audit(session, asset_id, 'revoked', operator, payload.reason, payload.expected_revision + 1)
        session.commit()
    except DiagnosticGrantConflict:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        logger.exception('Diagnostic revoke transaction failed')
        raise
    session.refresh(item)
    return item


def _confirmation(asset: Asset, payload: DiagnosticConfirmation) -> None:
    if payload.confirmation != asset.name:
        raise DiagnosticGrantInvalid('确认内容必须与服务器资产名称完全一致')


def _audit(session: Session, asset_id: str, action: str, operator: str, reason: str, revision: int) -> None:
    session.add(DiagnosticGrantAudit(id=str(uuid4()), asset_id=asset_id, action=action,
                                    operator=operator, reason=redact_context(reason), revision=revision))


def audit_history(session: Session, asset_id: str, limit: int = 50) -> dict[str, object]:
    rows = session.scalars(select(DiagnosticGrantAudit).where(DiagnosticGrantAudit.asset_id == asset_id)
                           .order_by(DiagnosticGrantAudit.created_at.desc(), DiagnosticGrantAudit.id.desc()).limit(limit))
    return {'items': [{'id': row.id, 'asset_id': row.asset_id, 'action': row.action,
                      'operator': row.operator, 'reason': row.reason, 'revision': row.revision,
                      'created_at': _utc(row.created_at)} for row in rows]}


def record_read(session: Session, asset_id: str, operator: str, trace_id: str,
                status: str, attempted_revision: object = None) -> None:
    grant = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset_id))
    if grant is not None:
        revision = int(str(attempted_revision)) if str(attempted_revision).isdigit() else 0
        _audit(session, asset_id, 'read_' + status, operator,
               f'Restricted observations; trace_id={trace_id}', revision)


def test_connectivity(
    session: Session, asset_id: str, vault: CredentialVault, operator: str,
    confirmation: str, reason: str, trace_id: str, expected_revision: int | None = None,
) -> dict[str, object]:
    """Run the configured read-only scope once and return metadata only."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise DiagnosticGrantInvalid('服务器资产不存在')
    grant = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset_id))
    if grant is None:
        raise DiagnosticGrantInvalid('尚未配置只读诊断授权')
    if confirmation != asset.name:
        raise DiagnosticGrantInvalid('确认内容必须与服务器资产名称完全一致')
    if expected_revision is not None and expected_revision != grant.revision:
        raise DiagnosticGrantConflict('授权已变化，请刷新后重试')
    try:
        require_grant(session, asset, grant.revision)
        result = _collect_for_grant(session, asset, grant, vault)
    except DiagnosticAuthorizationError:
        result = {
            'status': 'failed', 'summary': '只读授权当前不可用，未执行诊断。',
            'error_code': 'diagnostic_authorization_required', 'evidence': {},
        }
    except Exception as exc:  # Network and connector errors are summarized, never echoed.
        logger.warning('Diagnostic grant test failed for %s: %s', asset_id, type(exc).__name__)
        result = {
            'status': 'failed', 'summary': '只读连通性验证失败，需检查授权范围和目标服务。',
            'error_code': getattr(exc, 'code', 'diagnostic_test_failed'), 'evidence': {},
        }
    status = str(result.get('status', 'failed'))
    _audit(session, asset_id, 'read_test_' + status, operator,
           f'{redact_context(reason)}; trace_id={trace_id}', grant.revision)
    session.commit()
    evidence = result.get('evidence')
    return {
        'asset_id': asset_id, 'kind': grant.kind, 'status': status,
        'summary': str(result.get('summary', '只读连通性验证未完成。')),
        'error_code': result.get('error_code'),
        'read_only': True, 'grant_revision': grant.revision,
        'evidence_keys': sorted(evidence) if isinstance(evidence, dict) else [],
    }


def _collect_for_grant(
    session: Session, asset: Asset, grant: DiagnosticGrant, vault: CredentialVault,
) -> dict[str, object]:
    from src.services import alert_diagnostics, docker_diagnostics, kubernetes_diagnostics
    if grant.kind == 'kubernetes':
        return kubernetes_diagnostics.collect(asset, vault, session, expected_revision=grant.revision)
    if grant.kind == 'docker':
        return docker_diagnostics.collect(asset, vault, session, expected_revision=grant.revision)
    if grant.kind == 'mysql':
        from src.services import mysql_diagnostics
        return mysql_diagnostics.collect(asset, vault, session, expected_revision=grant.revision)
    if grant.kind == 'redis':
        from src.services import redis_diagnostics
        return redis_diagnostics.collect(asset, vault, session, expected_revision=grant.revision)
    if grant.kind == 'rabbitmq':
        from src.services import rabbitmq_diagnostics
        return rabbitmq_diagnostics.collect(asset, vault, session, expected_revision=grant.revision)
    return alert_diagnostics.collect(
        asset, vault, None, session, expected_revision=grant.revision
    )
