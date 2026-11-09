import ipaddress
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.services import alert_diagnostics, diagnostic_grants, kubernetes_grants
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.kubernetes_projection import project_list
from src.services.kubernetes_transport import (
    FAILURE_CODES, KubernetesTransport, KubernetesTransportError, NAMESPACE, TOTAL_SECONDS, endpoint_target,
)

POLICY_VERSION = 'readonly-kubernetes-2026.10.15'
MAX_EVIDENCE_CHARS = 12000
MAX_ITEMS = 150
MAX_PAGES = 3
ACTIONS = {'kubernetes.pods': ('pods',), 'kubernetes.events': ('events',),
           'kubernetes.workloads': ('deployments', 'statefulsets', 'daemonsets')}


@dataclass
class _Observation:
    revision: int
    evidence: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    items_read: int = 0

    def metadata(self) -> dict[str, str]:
        return {'collection_mode': 'restricted_read_only_kubernetes', 'read_only': 'true',
                'remediation': 'disabled', 'grant_revision': str(self.revision),
                'policy_version': POLICY_VERSION,
                'action_errors': json.dumps(self.errors, ensure_ascii=True)}

    def combined(self) -> dict[str, str]:
        return {**self.metadata(), **self.evidence}

    def store(self, key: str, namespace: str, rows: list[dict[str, object]]) -> None:
        encoded = json.dumps({'namespace': namespace, 'items': rows}, ensure_ascii=True,
                             separators=(',', ':'))
        other = sum(len(value) for name, value in self.evidence.items() if name != key)
        # Reserve space for every scoped action's fixed error code and mandatory metadata.
        if len(encoded) + other > MAX_EVIDENCE_CHARS - 2048:
            raise KubernetesTransportError('evidence_limit')
        self.evidence[key] = encoded


def _scope(asset: Asset, grant: DiagnosticGrant) -> tuple[list[str], list[str], str]:
    config = grant.kubernetes_config
    if grant.kind != 'kubernetes' or not isinstance(config, dict):
        raise KubernetesTransportError('invalid_configuration')
    actions, namespaces, endpoint = grant.action_ids, config.get('namespaces'), config.get('endpoint')
    if (not isinstance(actions, list) or not 1 <= len(actions) <= 3
        or any(not isinstance(action, str) or action not in ACTIONS for action in actions)
        or len(set(actions)) != len(actions) or not isinstance(namespaces, list)
        or not 1 <= len(namespaces) <= 3
        or any(not isinstance(name, str) or not NAMESPACE.fullmatch(name) for name in namespaces)
        or len(set(namespaces)) != len(namespaces) or not isinstance(endpoint, str)):
        raise KubernetesTransportError('invalid_configuration')
    host, _ = endpoint_target(endpoint)
    try:
        if ipaddress.ip_address(host) != ipaddress.ip_address(asset.host):
            raise ValueError('invalid')
    except ValueError:
        raise KubernetesTransportError('invalid_configuration') from None
    return actions, namespaces, endpoint


def _credentials(grant: DiagnosticGrant, vault: CredentialVault) -> tuple[str, str]:
    try:
        encoded = vault.decrypt(grant.encrypted_secret, grant.nonce, grant.id,
                                'diagnostic_kubernetes', grant.key_version)
        if len(encoded) > 128 * 1024:
            raise ValueError('invalid')
        secret = json.loads(encoded)
        if not isinstance(secret, dict) or set(secret) != {'token', 'ca_pem'}:
            raise ValueError('invalid')
        token, ca_pem = secret['token'], secret['ca_pem']
        if not isinstance(token, str) or not isinstance(ca_pem, str):
            raise ValueError('invalid')
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, RecursionError):
        raise KubernetesTransportError('credential_decrypt_failed') from None
    kubernetes_grants.validate_runtime_secret(grant, token, ca_pem)
    return token, ca_pem


def collect(
    asset: Asset, vault: CredentialVault, session: Session | None,
    guard: Callable[[], None] | None = None, expected_revision: int | None = None,
) -> dict[str, object]:
    deadline = time.monotonic() + TOTAL_SECONDS
    if guard is not None:
        guard()
    if session is None:
        raise alert_diagnostics.DiagnosticAuthorizationRequired(
            'Dedicated diagnostic authorization unavailable', expected_revision)
    try:
        grant = diagnostic_grants.require_grant(session, asset, expected_revision)
    except diagnostic_grants.DiagnosticAuthorizationError:
        raise alert_diagnostics.DiagnosticAuthorizationRequired(
            'Bound authorization unavailable', expected_revision) from None
    observation = _Observation(grant.revision)
    authorize = _deadline_guard(
        alert_diagnostics._authorization_guard(session, asset, grant.revision, guard), deadline)
    try:
        authorize()
        actions, namespaces, endpoint = _scope(asset, grant)
        token, ca_pem = _credentials(grant, vault)
        authorize()
        transport = KubernetesTransport(endpoint, ca_pem, token, deadline, authorize)
        _observe(transport, actions, namespaces, observation, authorize)
        authorize()
    except (alert_diagnostics.DiagnosticAuthorizationRequired,
            alert_diagnostics.DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        exc.evidence.update(observation.combined())
        raise
    except diagnostic_grants.DiagnosticAuthorizationError:
        raise alert_diagnostics.DiagnosticAuthorizationRequired(
            'Bound authorization unavailable', grant.revision, observation.combined()) from None
    except KubernetesTransportError as exc:
        observation.errors['collection'] = exc.code
    return _result(observation)


def _deadline_guard(authorize: Callable[[], None], deadline: float) -> Callable[[], None]:
    def bounded_authorize() -> None:
        authorize()
        if time.monotonic() >= deadline:
            raise KubernetesTransportError('timeout')
    return bounded_authorize


def _observe(transport: KubernetesTransport, actions: list[str], namespaces: list[str],
             observation: _Observation, authorize: Callable[[], None]) -> None:
    for namespace in namespaces:
        for action in actions:
            key = action + ':' + namespace
            rows: list[dict[str, object]] = []
            try:
                for resource in ACTIONS[action]:
                    _resource(transport, resource, namespace, key, rows, observation, authorize)
            except KubernetesTransportError as exc:
                observation.errors[key] = exc.code
            authorize()


def _resource(transport: KubernetesTransport, resource: str, namespace: str, key: str,
              rows: list[dict[str, object]], observation: _Observation,
              authorize: Callable[[], None]) -> None:
    continuation = ''
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        authorize()
        if observation.items_read >= MAX_ITEMS:
            raise KubernetesTransportError('item_limit')
        document = transport.request(namespace, resource, continuation)
        authorize()
        items, continuation = project_list(document, resource, namespace)
        if observation.items_read + len(items) > MAX_ITEMS:
            raise KubernetesTransportError('item_limit')
        observation.items_read += len(items)
        if not items:
            observation.store(key, namespace, rows)
        for item in items:
            observation.store(key, namespace, rows + [item])
            rows.append(item)
        authorize()
        if not continuation:
            return
        if continuation in seen:
            raise KubernetesTransportError('pagination_loop')
        seen.add(continuation)
        if page == MAX_PAGES - 1:
            raise KubernetesTransportError('page_limit')


def _result(observation: _Observation) -> dict[str, object]:
    status = 'failed' if not observation.evidence else 'partial' if observation.errors else 'completed'
    summary = {
        'failed': '\u53ea\u8bfb K8s \u91c7\u96c6\u5931\u8d25\uff0c\u9700\u7ba1\u7406\u5458\u4eba\u5de5\u6392\u67e5\u3002',
        'partial': '\u5df2\u4fdd\u7559\u90e8\u5206 K8s \u53ea\u8bfb\u8bc1\u636e\uff1b\u91c7\u96c6\u4e0d\u5b8c\u6574\uff0c\u9700\u4eba\u5de5\u6838\u5b9e\u3002',
        'completed': '\u5df2\u53d6\u5f97\u6388\u6743\u8303\u56f4\u7684 K8s \u53ea\u8bfb\u8bc1\u636e\uff0c\u6839\u56e0\u4e0e\u6062\u590d\u4ecd\u9700\u4eba\u5de5\u6838\u5b9e\u3002',
    }[status]
    result = {'status': status, 'summary': summary, 'evidence': observation.combined()}
    if status == 'failed':
        code = next(iter(observation.errors.values()), 'transport_failed')
        code = code if code in FAILURE_CODES else 'transport_failed'
        result['error_code'] = code
        result['evidence']['error_code'] = code
    return result
