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
from src.schemas.rabbitmq_grant import RABBITMQ_ACTIONS, RABBITMQ_USER, RabbitMQGrantInput, safe_host
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.readonly_diagnostics import ReadOnlyPolicyError

SECRET_TYPE = 'diagnostic_rabbitmq'
logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def effective_status(grant: DiagnosticGrant, asset: Asset) -> str:
    if not grant.enabled:
        return 'disabled'
    try:
        require_scope(grant)
        if safe_host(asset.host) != grant.host:
            return 'target_changed'
    except (ValueError, TypeError):
        return 'target_changed'
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        return 'asset_disabled'
    return 'expired' if _utc(grant.expires_at) <= datetime.now(timezone.utc) else 'active'


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
        raise ValueError('RabbitMQ CA 证书无效或有效期不足') from exc


def _decrypt_secret(grant: DiagnosticGrant, vault: CredentialVault) -> dict[str, str]:
    try:
        secret = json.loads(vault.decrypt(
            grant.encrypted_secret, grant.nonce, grant.id, SECRET_TYPE, grant.key_version))
        if (not isinstance(secret, dict) or set(secret) != {'password', 'ca_pem'}
                or not all(isinstance(value, str) and value for value in secret.values())
                or len(secret['password']) > 4096):
            raise ValueError
        return secret
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError('RabbitMQ 专用凭据不可用') from None


def _secret_values(
    payload: RabbitMQGrantInput, previous: DiagnosticGrant | None, identifier: str,
    vault: CredentialVault, host: str,
) -> dict[str, object]:
    old = previous.rabbitmq_config if previous is not None else {}
    old = old if isinstance(old, dict) else {}
    changed = previous is None or any(old.get(key) != value for key, value in {
        'host': host, 'port': payload.port, 'username': payload.username, 'vhost': payload.vhost,
    }.items())
    if previous is not None and changed and payload.password is None:
        raise ValueError('变更 RabbitMQ 身份、端口或 vhost 时必须提供新密码')
    if previous is None and (payload.password is None or payload.ca_pem is None):
        raise ValueError('首次授权必须提供 RabbitMQ 密码和 CA 证书')
    retained = (_decrypt_secret(previous, vault) if previous is not None
                and (payload.password is None or payload.ca_pem is None) else {})
    password = payload.password if payload.password is not None else retained.get('password')
    ca_pem = payload.ca_pem if payload.ca_pem is not None else retained.get('ca_pem')
    if not isinstance(password, str) or not isinstance(ca_pem, str):
        raise ValueError('RabbitMQ 密码和 CA 证书不可为空')
    fingerprint = _validate_ca(ca_pem, payload.expires_at)
    if previous is not None and fingerprint != old.get('ca_fingerprint') and payload.password is None:
        raise ValueError('变更 RabbitMQ CA 时必须提供密码重新确认')
    encrypted, nonce = vault.encrypt(
        json.dumps({'password': password, 'ca_pem': ca_pem}, ensure_ascii=False), identifier, SECRET_TYPE)
    return {'encrypted_secret': encrypted, 'nonce': nonce, 'key_version': vault.key_version,
            'key_fingerprint': '', 'ca_fingerprint': fingerprint}


def upsert(session: Session, payload: RabbitMQGrantInput, vault: CredentialVault, operator: str) -> DiagnosticGrant:
    from src.services import diagnostic_grants as grants
    asset = session.get(Asset, payload.asset_id)
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise grants.DiagnosticGrantInvalid('请选择启用的服务器资产')
    grants._confirmation(asset, payload)
    try:
        host = safe_host(asset.host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid('RabbitMQ 资产地址必须是允许的字面量 IP') from exc
    previous = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
    if previous is not None and previous.kind != 'rabbitmq':
        raise grants.DiagnosticGrantConflict('已有授权类型不可转换，请选择独立 RabbitMQ 映射资产')
    if (previous.revision if previous else 0) != payload.expected_revision:
        raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
    identifier = previous.id if previous else str(uuid4())
    try:
        values = _secret_values(payload, previous, identifier, vault, host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid(str(exc)) from None
    values.update(
        kind='rabbitmq', host=host, port=payload.port, username=payload.username,
        rabbitmq_config={'host': host, 'port': payload.port, 'username': payload.username,
                         'vhost': payload.vhost, 'ca_fingerprint': values.pop('ca_fingerprint')},
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
                DiagnosticGrant.kind == 'rabbitmq',
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
        logger.exception('RabbitMQ diagnostic authorization transaction failed')
        raise
    session.refresh(previous)
    return previous


def require_scope(grant: DiagnosticGrant) -> None:
    config = grant.rabbitmq_config
    if (grant.kind != 'rabbitmq' or not isinstance(config, dict)
            or not isinstance(config.get('host'), str)
            or type(config.get('port')) is not int or not 1 <= config['port'] <= 65535
            or not isinstance(config.get('username'), str) or not RABBITMQ_USER.fullmatch(config['username'])
            or config['username'] in {'root', 'admin', 'administrator', 'guest'}
            or not isinstance(config.get('vhost'), str) or not 1 <= len(config['vhost']) <= 128
            or config['vhost'] in {'.', '..'}
            or any(ord(char) < 32 or ord(char) == 127 for char in config['vhost'])
            or not isinstance(config.get('ca_fingerprint'), str)
            or not re.fullmatch(r'SHA256:[0-9a-f]{64}', config['ca_fingerprint'])
            or not isinstance(grant.action_ids, list) or not 1 <= len(grant.action_ids) <= 4
            or any(not isinstance(action, str) or action not in RABBITMQ_ACTIONS for action in grant.action_ids)
            or len(set(grant.action_ids)) != len(grant.action_ids)):
        raise ReadOnlyPolicyError('RabbitMQ 诊断范围无效')
    try:
        if (safe_host(config['host']) != config['host'] or grant.host != config['host']
                or grant.port != config['port'] or grant.username != config['username']):
            raise ValueError
    except ValueError:
        raise ReadOnlyPolicyError('RabbitMQ 诊断目标无效') from None


def serialize_config(grant: DiagnosticGrant) -> dict[str, object]:
    config = grant.rabbitmq_config or {}
    return {'kind': 'rabbitmq', 'password_masked': '********',
            'ca_fingerprint': config.get('ca_fingerprint', ''),
            'rabbitmq_config': {key: config.get(key) for key in ('host', 'port', 'username', 'vhost')}}


def load_secret(grant: DiagnosticGrant, vault: CredentialVault) -> tuple[str, str]:
    secret = _decrypt_secret(grant, vault)
    if _validate_ca(secret['ca_pem'], _utc(grant.expires_at)) != (grant.rabbitmq_config or {}).get('ca_fingerprint'):
        raise ValueError('RabbitMQ CA 证书指纹已变化')
    return secret['password'], secret['ca_pem']
