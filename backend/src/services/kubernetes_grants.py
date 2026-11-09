import base64
import hashlib
import ipaddress
import json
import logging
import ssl
from datetime import datetime, timezone
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.diagnostic_grant import DiagnosticGrant
from src.schemas.kubernetes_grant import KubernetesGrantInput, endpoint_target
from src.services.crypto import CredentialVault

SECRET_TYPE = 'diagnostic_kubernetes'
logger = logging.getLogger(__name__)


def effective_status(grant: DiagnosticGrant, asset: Asset) -> str:
    from src.services.diagnostic_grants import _utc
    config = grant.kubernetes_config or {}
    try:
        host, port = endpoint_target(str(config['endpoint']))
        if (host, port) != (grant.host, grant.port) or ipaddress.ip_address(asset.host) != ipaddress.ip_address(host):
            return 'target_changed'
        if asset.credential_id is not None or not config.get('namespaces'):
            return 'disabled'
        if _utc(grant.expires_at) <= datetime.now(timezone.utc):
            return 'expired'
    except (KeyError, ValueError, TypeError):
        return 'target_changed'
    return 'active'


def _stored_payload(grant: DiagnosticGrant) -> KubernetesGrantInput:
    from src.services.diagnostic_grants import DiagnosticAuthorizationError
    config = grant.kubernetes_config or {}
    try:
        # Validate stored nonsecret configuration too, not just newly submitted forms.
        return KubernetesGrantInput.model_validate({
            'kind': 'kubernetes', 'asset_id': grant.asset_id, 'expected_revision': grant.revision,
            'confirmation': 'stored', 'reason': 'stored authorization',
            'action_ids': grant.action_ids, 'expires_at': grant.expires_at.replace(tzinfo=timezone.utc)
            if grant.expires_at.tzinfo is None else grant.expires_at,
            'endpoint': config['endpoint'], 'service_account_namespace': config['service_account_namespace'],
            'service_account_name': config['service_account_name'], 'namespaces': config['namespaces'],
            'rbac_confirmed': True,
        })
    except (ValueError, KeyError, TypeError) as exc:
        raise DiagnosticAuthorizationError('Kubernetes diagnostic scope invalid') from exc


def require_scope(grant: DiagnosticGrant) -> None:
    _stored_payload(grant)


def validate_runtime_secret(grant: DiagnosticGrant, token: str, ca_pem: str) -> None:
    from src.services.diagnostic_grants import DiagnosticAuthorizationError, DiagnosticGrantInvalid
    payload = _stored_payload(grant)
    try:
        fingerprint = _validate_secret(payload, token, ca_pem)
    except DiagnosticGrantInvalid as exc:
        raise DiagnosticAuthorizationError('Kubernetes diagnostic identity unavailable') from exc
    if fingerprint != (grant.kubernetes_config or {}).get('ca_fingerprint'):
        raise DiagnosticAuthorizationError('Kubernetes diagnostic trust changed')


def serialize_config(grant: DiagnosticGrant) -> dict[str, object]:
    config = grant.kubernetes_config or {}
    return {'kind': 'kubernetes', 'token_masked': '********',
            'ca_fingerprint': config.get('ca_fingerprint', ''),
            'kubernetes_config': {key: config.get(key) for key in (
                'endpoint', 'service_account_namespace', 'service_account_name', 'namespaces')}}


def _validate_secret(payload: KubernetesGrantInput, token: str, ca_pem: str) -> str:
    from src.services.diagnostic_grants import DiagnosticGrantInvalid
    try:
        parts = token.split('.')
        if len(parts) != 3 or any(not part for part in parts) or any(char.isspace() for char in token):
            raise ValueError('Invalid token')
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        if not isinstance(claims, dict):
            raise ValueError('Invalid token claims')
        subject = f'system:serviceaccount:{payload.service_account_namespace}:{payload.service_account_name}'
        expiry = claims.get('exp')
        if (claims.get('sub') != subject or type(expiry) is not int
                or expiry < payload.expires_at.timestamp()):
            raise ValueError('Token subject or expiry mismatch')
        certs = x509.load_pem_x509_certificates(ca_pem.encode())
        if not 1 <= len(certs) <= 5 or 'PRIVATE KEY' in ca_pem:
            raise ValueError('Invalid CA')
        for cert in certs:
            if (not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                    or cert.not_valid_after_utc < payload.expires_at
                    or cert.not_valid_before_utc > datetime.now(timezone.utc)):
                raise ValueError('Invalid CA constraints')
        ssl.create_default_context(cadata=ca_pem)
        return 'SHA256:' + hashlib.sha256(b''.join(cert.fingerprint(hashes.SHA256()) for cert in certs)).hexdigest()
    except (ValueError, TypeError, KeyError, x509.ExtensionNotFound, ssl.SSLError) as exc:
        raise DiagnosticGrantInvalid('专用 ServiceAccount token 或 CA 无效；请检查身份、证书及有效期') from exc


def _secret_values(payload: KubernetesGrantInput, previous: DiagnosticGrant | None,
                   identifier: str, vault: CredentialVault) -> dict[str, object]:
    from src.services.diagnostic_grants import DiagnosticGrantInvalid
    config = {'endpoint': payload.endpoint, 'service_account_namespace': payload.service_account_namespace,
              'service_account_name': payload.service_account_name, 'namespaces': payload.namespaces}
    old = previous.kubernetes_config if previous is not None else None
    changed = old is None or any(config[key] != old.get(key) for key in (
        'endpoint', 'service_account_namespace', 'service_account_name'))
    if (changed or payload.ca_pem is not None) and payload.token is None:
        raise DiagnosticGrantInvalid('首次授权或变更端点、身份、CA 时必须提供新的专用 token')
    if previous is None and payload.ca_pem is None:
        raise DiagnosticGrantInvalid('首次授权必须提供专用 CA')
    retained = {}
    if previous is not None:
        retained = json.loads(vault.decrypt(previous.encrypted_secret, previous.nonce, identifier,
                                             SECRET_TYPE, previous.key_version))
    if previous is not None and (changed or payload.ca_pem is not None) and payload.token == retained.get('token'):
        raise DiagnosticGrantInvalid('变更端点、身份或 CA 时不能重复使用原 token')
    token, ca = payload.token or retained.get('token'), payload.ca_pem or retained.get('ca_pem')
    if not isinstance(token, str) or not isinstance(ca, str):
        raise DiagnosticGrantInvalid('专用 token 和 CA 均为必填项')
    config['ca_fingerprint'] = _validate_secret(payload, token, ca)
    encrypted, nonce = vault.encrypt(json.dumps({'token': token, 'ca_pem': ca}), identifier, SECRET_TYPE)
    return {'kubernetes_config': config, 'encrypted_secret': encrypted, 'nonce': nonce,
            'key_version': vault.key_version, 'key_fingerprint': '********'}


def upsert(session: Session, payload: KubernetesGrantInput, vault: CredentialVault,
           operator: str) -> DiagnosticGrant:
    from src.services import diagnostic_grants as grants
    asset = session.get(Asset, payload.asset_id)
    if asset is None or asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise grants.DiagnosticGrantInvalid('请选择启用的集群映射资产')
    grants._confirmation(asset, payload)
    host, port = endpoint_target(payload.endpoint)
    try:
        matched = ipaddress.ip_address(asset.host) == ipaddress.ip_address(host)
    except ValueError:
        matched = False
    if not matched or asset.credential_id is not None:
        raise grants.DiagnosticGrantInvalid('API 地址必须等于资产 IP，且集群映射资产不能绑定人工 SSH 凭据')
    previous = session.scalar(select(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id))
    if previous is not None and previous.kind != 'kubernetes':
        raise grants.DiagnosticGrantConflict('已有授权类型不可转换，请选择独立集群映射资产')
    if (previous.revision if previous else 0) != payload.expected_revision:
        raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
    identifier = previous.id if previous else str(uuid4())
    values = _secret_values(payload, previous, identifier, vault)
    values.update(kind='kubernetes', host=host, port=port, username=payload.service_account_name,
                  host_fingerprint='', host_public_key='', host_key_type='', action_ids=payload.action_ids,
                  expires_at=payload.expires_at, enabled=True, revision=payload.expected_revision + 1,
                  operator=operator, updated_at=datetime.now(timezone.utc))
    return _save(session, previous, identifier, asset.id, values, payload, operator)


def _save(session: Session, previous: DiagnosticGrant | None, identifier: str, asset_id: str,
          values: dict[str, object], payload: KubernetesGrantInput, operator: str) -> DiagnosticGrant:
    from src.services import diagnostic_grants as grants
    try:
        if previous is None:
            previous = DiagnosticGrant(id=identifier, asset_id=asset_id, **values)
            session.add(previous)
        else:
            count = session.execute(update(DiagnosticGrant).where(
                DiagnosticGrant.id == identifier, DiagnosticGrant.revision == payload.expected_revision,
                DiagnosticGrant.kind == 'kubernetes',
            ).values(**values), execution_options={'synchronize_session': False}).rowcount
            if count != 1:
                raise grants.DiagnosticGrantConflict('授权已变化，请刷新后重试')
        grants._audit(session, asset_id, 'created' if payload.expected_revision == 0 else 'reconfigured',
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
        logger.exception('Kubernetes authorization transaction failed')
        raise
    session.refresh(previous)
    return previous
