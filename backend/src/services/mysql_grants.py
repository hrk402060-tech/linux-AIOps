import hashlib
import json
import logging
import re
import ssl
from datetime import datetime, timezone
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.diagnostic_grant import DiagnosticGrant
from src.schemas.mysql_grant import MYSQL_ACTIONS, MYSQL_DATABASE, MYSQL_USER, MySQLGrantInput, _safe_host
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.readonly_diagnostics import ReadOnlyPolicyError

SECRET_TYPE = 'diagnostic_mysql'
logger = logging.getLogger(__name__)


def effective_status(grant: DiagnosticGrant, asset: Asset) -> str:
    if not grant.enabled:
        return 'disabled'
    config = grant.mysql_config or {}
    try:
        host = _safe_host(asset.host)
        if (grant.kind != 'mysql' or str(config['host']) != host or grant.host != host
                or type(config['port']) is not int or config['port'] != grant.port
                or grant.username != config['username']):
            return 'target_changed'
    except (KeyError, TypeError, ValueError):
        return 'target_changed'
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        return 'asset_disabled'
    if _utc(grant.expires_at) <= datetime.now(timezone.utc):
        return 'expired'
    return 'active'


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _validate_ca(ca_pem: str, expires_at: datetime) -> str:
    try:
        if len(ca_pem) > 32768 or not re.fullmatch(
            r'\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+'
            r'-----END CERTIFICATE-----\s*){1,5}', ca_pem,
        ):
            raise ValueError
        certs = x509.load_pem_x509_certificates(ca_pem.encode())
        if not 1 <= len(certs) <= 5 or 'PRIVATE KEY' in ca_pem:
            raise ValueError
        context = ssl.create_default_context(cadata=ca_pem)
        context.check_hostname = True
        for cert in certs:
            if (not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                    or cert.not_valid_after_utc < _utc(expires_at)
                    or cert.not_valid_before_utc > datetime.now(timezone.utc)):
                raise ValueError
        return 'SHA256:' + hashlib.sha256(
            b''.join(cert.fingerprint(hashes.SHA256()) for cert in certs)
        ).hexdigest()
    except (ValueError, TypeError, ssl.SSLError, x509.ExtensionNotFound) as exc:
        raise ValueError('MySQL CA 证书无效或有效期不足') from exc


def _decrypt_secret(grant: DiagnosticGrant, vault: CredentialVault) -> dict[str, str]:
    try:
        raw = vault.decrypt(grant.encrypted_secret, grant.nonce, grant.id, SECRET_TYPE, grant.key_version)
        if len(raw) > 128 * 1024:
            raise ValueError
        secret = json.loads(raw)
        if (not isinstance(secret, dict) or set(secret) != {'password', 'ca_pem'}
                or not all(isinstance(value, str) and value for value in secret.values())
                or len(secret['password']) > 4096):
            raise ValueError
        return secret
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError('MySQL 专用凭据不可用') from None


def _secret_values(
    payload: MySQLGrantInput, previous: DiagnosticGrant | None, identifier: str, vault: CredentialVault, host: str,
) -> dict[str, object]:
    old = previous.mysql_config if previous is not None else None
    old = old if isinstance(old, dict) else {}
    changed_identity = (previous is None or str(old.get('username', '')) != payload.username
                        or old.get('host') != host)
    try:
        changed_identity = changed_identity or int(old.get('port', payload.port)) != payload.port
    except (TypeError, ValueError):
        changed_identity = True
    if previous is not None and changed_identity and payload.password is None:
        raise ValueError('变更 MySQL 身份或端口时必须提供新密码')
    if previous is None and (payload.password is None or payload.ca_pem is None):
        raise ValueError('首次授权必须提供 MySQL 密码和 CA 证书')
    retained = (_decrypt_secret(previous, vault) if previous is not None
                and (payload.password is None or payload.ca_pem is None) else {})
    password = payload.password or retained.get('password')
    ca_pem = payload.ca_pem or retained.get('ca_pem')
    if not isinstance(password, str) or not isinstance(ca_pem, str):
        raise ValueError('MySQL 密码和 CA 证书不可为空')
    fingerprint = _validate_ca(ca_pem, payload.expires_at)
    if previous is not None and fingerprint != old.get('ca_fingerprint') and payload.password is None:
        raise ValueError('变更 MySQL CA 时必须提供密码重新确认')
    encrypted, nonce = vault.encrypt(
        json.dumps({'password': password, 'ca_pem': ca_pem}, ensure_ascii=False),
        identifier, SECRET_TYPE,
    )
    return {
        'encrypted_secret': encrypted, 'nonce': nonce, 'key_version': vault.key_version,
        'key_fingerprint': '',
        'ca_fingerprint': fingerprint,
    }


def upsert(session: Session, payload: MySQLGrantInput, vault: CredentialVault, operator: str) -> DiagnosticGrant:
    from src.services import diagnostic_grants as grants
    asset = session.get(Asset, payload.asset_id)
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise grants.DiagnosticGrantInvalid('请选择启用的服务器资产')
    grants._confirmation(asset, payload)
    try:
        host = _safe_host(asset.host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid('MySQL 资产地址必须是字面量 IP') from exc
    previous = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
    if previous is not None and previous.kind != 'mysql':
        raise grants.DiagnosticGrantConflict('已有授权类型不可转换，请选择独立 MySQL 映射资产')
    if (previous.revision if previous else 0) != payload.expected_revision:
        raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
    identifier = previous.id if previous else str(uuid4())
    try:
        values = _secret_values(payload, previous, identifier, vault, host)
    except ValueError as exc:
        raise grants.DiagnosticGrantInvalid(str(exc)) from None
    values.update(
        kind='mysql', host=host, port=payload.port, username=payload.username,
        mysql_config={
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
                DiagnosticGrant.kind == 'mysql',
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
        logger.exception('MySQL diagnostic authorization transaction failed')
        raise
    session.refresh(previous)
    return previous


def require_scope(grant: DiagnosticGrant) -> None:
    config = grant.mysql_config
    if (grant.kind != 'mysql' or not isinstance(config, dict)
            or not isinstance(config.get('host'), str)
            or type(config.get('port')) is not int or not 1 <= config['port'] <= 65535
            or not isinstance(config.get('username'), str)
            or not MYSQL_USER.fullmatch(config['username'])
            or config['username'] in {'root', 'admin', 'mysql', 'administrator'}
            or not isinstance(config.get('database'), str)
            or (config['database'] and not MYSQL_DATABASE.fullmatch(config['database']))
            or not isinstance(config.get('ca_fingerprint'), str)
            or not re.fullmatch(r'SHA256:[0-9a-f]{64}', config['ca_fingerprint'])
            or not isinstance(grant.action_ids, list) or not 1 <= len(grant.action_ids) <= 2
            or any(not isinstance(action, str) or action not in MYSQL_ACTIONS for action in grant.action_ids)
            or len(set(grant.action_ids)) != len(grant.action_ids)):
        raise ReadOnlyPolicyError('MySQL 诊断范围无效')
    try:
        if (_safe_host(config['host']) != config['host'] or grant.host != config['host']
                or grant.port != config['port'] or grant.username != config['username']):
            raise ValueError
    except ValueError:
        raise ReadOnlyPolicyError('MySQL 诊断目标无效') from None


def serialize_config(grant: DiagnosticGrant) -> dict[str, object]:
    config = grant.mysql_config or {}
    return {
        'kind': 'mysql', 'password_masked': '********',
        'ca_fingerprint': config.get('ca_fingerprint', ''),
        'mysql_config': {key: config.get(key) for key in ('host', 'port', 'username', 'database')},
    }


def load_secret(grant: DiagnosticGrant, vault: CredentialVault) -> tuple[str, str]:
    secret = _decrypt_secret(grant, vault)
    if _validate_ca(secret['ca_pem'], _utc(grant.expires_at)) != str((grant.mysql_config or {}).get('ca_fingerprint')):
        raise ValueError('MySQL CA 证书指纹已变化')
    return secret['password'], secret['ca_pem']
