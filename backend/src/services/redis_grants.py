import hashlib
import json
import logging
import re
import ssl
from datetime import datetime, timezone
from uuid import uuid4

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.diagnostic_grant import DiagnosticGrant
from src.schemas.redis_grant import REDIS_ACTIONS, REDIS_USER, RedisGrantInput, safe_host
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.readonly_diagnostics import ReadOnlyPolicyError

SECRET_TYPE = 'diagnostic_redis'
logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def effective_status(grant: DiagnosticGrant, asset: Asset) -> str:
    if not grant.enabled:
        return 'disabled'
    config = grant.redis_config or {}
    try:
        host = safe_host(asset.host)
        if (grant.kind != 'redis' or config.get('host') != host or grant.host != host
                or type(config.get('port')) is not int or config['port'] != grant.port
                or config.get('username') != grant.username
                or type(config.get('database')) is not int):
            return 'target_changed'
    except (KeyError, TypeError, ValueError):
        return 'target_changed'
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        return 'asset_disabled'
    if _utc(grant.expires_at) <= datetime.now(timezone.utc):
        return 'expired'
    return 'active'


def _validate_ca(ca_pem: str, expires_at: datetime) -> str:
    try:
        if len(ca_pem) > 32768 or 'PRIVATE KEY' in ca_pem:
            raise ValueError
        certs = x509.load_pem_x509_certificates(ca_pem.encode())
        if not 1 <= len(certs) <= 5:
            raise ValueError
        for cert in certs:
            if (not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                    or cert.not_valid_after_utc < _utc(expires_at)
                    or cert.not_valid_before_utc > datetime.now(timezone.utc)):
                raise ValueError
        ssl.create_default_context(cadata=ca_pem)
        return 'SHA256:' + hashlib.sha256(
            b''.join(cert.fingerprint(hashes.SHA256()) for cert in certs)
        ).hexdigest()
    except (ValueError, TypeError, ssl.SSLError, x509.ExtensionNotFound) as exc:
        raise ValueError('Redis CA 证书无效或有效期不足') from exc


def _decrypt_secret(grant: DiagnosticGrant, vault: CredentialVault) -> dict[str, str]:
    try:
        raw = vault.decrypt(grant.encrypted_secret, grant.nonce, grant.id, SECRET_TYPE, grant.key_version)
        secret = json.loads(raw)
        if (not isinstance(secret, dict) or set(secret) != {'password', 'ca_pem'}
                or not all(isinstance(value, str) and value for value in secret.values())
                or len(secret['password']) > 4096):
            raise ValueError
        return secret
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError('Redis 专用凭据不可用') from None


def _secret_values(
    payload: RedisGrantInput, previous: DiagnosticGrant | None, identifier: str,
    vault: CredentialVault, host: str,
) -> dict[str, object]:
    old = previous.redis_config if previous is not None else None
    old = old if isinstance(old, dict) else {}
    changed = previous is None or any(old.get(key) != value for key, value in {
        'host': host, 'port': payload.port, 'username': payload.username, 'database': payload.database,
    }.items())
    if previous is not None and changed and payload.password is None:
        raise ValueError('变更 Redis 身份、端口或数据库时必须提供新密码')
    if previous is None and (payload.password is None or payload.ca_pem is None):
        raise ValueError('首次授权必须提供 Redis 密码和 CA 证书')
    retained = (_decrypt_secret(previous, vault) if previous is not None
                and (payload.password is None or payload.ca_pem is None) else {})
    password, ca_pem = payload.password or retained.get('password'), payload.ca_pem or retained.get('ca_pem')
    if not isinstance(password, str) or not isinstance(ca_pem, str):
        raise ValueError('Redis 密码和 CA 证书不可为空')
    fingerprint = _validate_ca(ca_pem, payload.expires_at)
    if previous is not None and fingerprint != old.get('ca_fingerprint') and payload.password is None:
        raise ValueError('变更 Redis CA 时必须提供密码重新确认')
    encrypted, nonce = vault.encrypt(
        json.dumps({'password': password, 'ca_pem': ca_pem}, ensure_ascii=False),
        identifier, SECRET_TYPE,
    )
    return {
        'encrypted_secret': encrypted, 'nonce': nonce, 'key_version': vault.key_version,
        'key_fingerprint': '', 'ca_fingerprint': fingerprint,
    }


def upsert(session: Session, payload: RedisGrantInput, vault: CredentialVault, operator: str) -> DiagnosticGrant:
    from src.services import diagnostic_grants as grants
    asset = session.get(Asset, payload.asset_id)
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise grants.DiagnosticGrantInvalid('请选择启用的服务器资产')
    grants._confirmation(asset, payload)
    try:
        host = safe_host(asset.host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid('Redis 资产地址必须是字面量 IP') from exc
    previous = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
    if previous is not None and previous.kind != 'redis':
        raise grants.DiagnosticGrantConflict('已有授权类型不可转换，请选择独立 Redis 映射资产')
    if (previous.revision if previous else 0) != payload.expected_revision:
        raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
    identifier = previous.id if previous else str(uuid4())
    try:
        values = _secret_values(payload, previous, identifier, vault, host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid(str(exc)) from None
    values.update(
        kind='redis', host=host, port=payload.port, username=payload.username,
        redis_config={
            'host': host, 'port': payload.port, 'username': payload.username,
            'database': payload.database, 'ca_fingerprint': values.pop('ca_fingerprint'),
        },
        host_fingerprint='', host_public_key='', host_key_type='',
        action_ids=payload.action_ids, expires_at=payload.expires_at, enabled=True,
        revision=payload.expected_revision + 1, operator=operator, updated_at=datetime.now(timezone.utc),
    )
    try:
        if previous is None:
            previous = DiagnosticGrant(id=identifier, asset_id=asset.id, **values)
            session.add(previous)
        else:
            count = session.execute(update(DiagnosticGrant).where(
                DiagnosticGrant.id == identifier, DiagnosticGrant.revision == payload.expected_revision,
                DiagnosticGrant.kind == 'redis',
            ).values(**values), execution_options={'synchronize_session': False}).rowcount
            if count != 1:
                raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
        grants._audit(session, asset.id, 'created' if payload.expected_revision == 0 else 'reconfigured',
                      operator, payload.reason, payload.expected_revision + 1)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试') from exc
    except grants.DiagnosticGrantConflict:
        session.rollback()
        raise
    except SQLAlchemyError:
        session.rollback()
        logger.exception('Redis diagnostic authorization transaction failed')
        raise
    session.refresh(previous)
    return previous


def require_scope(grant: DiagnosticGrant) -> None:
    config = grant.redis_config
    if (grant.kind != 'redis' or not isinstance(config, dict)
            or not isinstance(config.get('host'), str)
            or type(config.get('port')) is not int or not 1 <= config['port'] <= 65535
            or not isinstance(config.get('username'), str) or not REDIS_USER.fullmatch(config['username'])
            or config['username'] in {'root', 'admin', 'administrator', 'default'}
            or type(config.get('database')) is not int or not 0 <= config['database'] <= 15
            or not isinstance(config.get('ca_fingerprint'), str)
            or not re.fullmatch(r'SHA256:[0-9a-f]{64}', config['ca_fingerprint'])
            or not isinstance(grant.action_ids, list) or not 1 <= len(grant.action_ids) <= 4
            or any(not isinstance(action, str) or action not in REDIS_ACTIONS for action in grant.action_ids)
            or len(set(grant.action_ids)) != len(grant.action_ids)):
        raise ReadOnlyPolicyError('Redis 诊断范围无效')
    try:
        if (safe_host(config['host']) != config['host'] or grant.host != config['host']
                or grant.port != config['port'] or grant.username != config['username']):
            raise ValueError
    except ValueError:
        raise ReadOnlyPolicyError('Redis 诊断目标无效') from None


def serialize_config(grant: DiagnosticGrant) -> dict[str, object]:
    config = grant.redis_config or {}
    return {'kind': 'redis', 'password_masked': '********',
            'ca_fingerprint': config.get('ca_fingerprint', ''),
            'redis_config': {key: config.get(key) for key in ('host', 'port', 'username', 'database')}}


def load_secret(grant: DiagnosticGrant, vault: CredentialVault) -> tuple[str, str]:
    secret = _decrypt_secret(grant, vault)
    if _validate_ca(secret['ca_pem'], _utc(grant.expires_at)) != str((grant.redis_config or {}).get('ca_fingerprint')):
        raise ValueError('Redis CA 证书指纹已变化')
    return secret['password'], secret['ca_pem']
