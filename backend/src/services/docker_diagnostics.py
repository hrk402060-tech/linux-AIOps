import ipaddress
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.services import alert_diagnostics, diagnostic_grants, docker_grants
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.docker_projection import MAX_PROJECTED, project_containers, project_stats
from src.services.docker_transport import (
    CONTAINER_ID, DockerTransport, DockerTransportError, FAILURE_CODES, MAX_CONTAINERS, TOTAL_SECONDS,
)

POLICY_VERSION = 'readonly-docker-2026.10.15'
MAX_EVIDENCE_CHARS = 12000
ACTIONS = frozenset({'docker.containers', 'docker.resources'})


@dataclass
class _Observation:
    revision: int
    evidence: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def combined(self) -> dict[str, str]:
        return {'collection_mode': 'restricted_read_only_docker',
                'read_only': 'true', 'remediation': 'disabled',
                'grant_revision': str(self.revision), 'policy_version': POLICY_VERSION,
                'action_errors': json.dumps(self.errors, ensure_ascii=True), **self.evidence}

    def store(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        other = sum(len(item) for name, item in self.evidence.items() if name != key)
        if len(encoded) + other > MAX_EVIDENCE_CHARS - 2048:
            raise DockerTransportError('evidence_limit')
        self.evidence[key] = encoded


def _credentials(grant: DiagnosticGrant, vault: CredentialVault) -> tuple[str, str]:
    try:
        raw = vault.decrypt(grant.encrypted_secret, grant.nonce, grant.id,
                             docker_grants.SECRET_TYPE, grant.key_version)
        if len(raw) > 128 * 1024:
            raise ValueError
        secret = json.loads(raw)
        if not isinstance(secret, dict):
            raise ValueError
        token, ca = secret['token'], secret['ca_pem']
        if set(secret) != {'token', 'ca_pem'} or not isinstance(token, str) or not isinstance(ca, str):
            raise ValueError
    except (CredentialCryptoError, InvalidTag, ValueError, TypeError, KeyError, RecursionError):
        raise DockerTransportError('credential_decrypt_failed') from None
    docker_grants.validate_runtime_secret(grant, token, ca)
    return token, ca


def _scope(asset: Asset, grant: DiagnosticGrant) -> tuple[list[str], str]:
    config = grant.docker_config
    if not isinstance(config, dict):
        raise DockerTransportError('invalid_configuration')
    actions, endpoint = grant.action_ids, config.get('endpoint')
    if (grant.kind != 'docker' or not isinstance(actions, list) or not actions
            or len(actions) > 2 or any(not isinstance(action, str) or action not in ACTIONS for action in actions)
            or len(set(actions)) != len(actions)
            or not isinstance(endpoint, str)):
        raise DockerTransportError('invalid_configuration')
    try:
        host, _ = docker_grants.endpoint_target(endpoint)
        if ipaddress.ip_address(host) != ipaddress.ip_address(asset.host):
            raise ValueError
    except ValueError:
        raise DockerTransportError('invalid_configuration') from None
    return actions, endpoint


def collect(asset: Asset, vault: CredentialVault, session: Session | None,
            guard: Callable[[], None] | None = None, expected_revision: int | None = None) -> dict[str, object]:
    deadline = time.monotonic() + TOTAL_SECONDS
    if guard is not None:
        guard()
    if session is None:
        raise alert_diagnostics.DiagnosticAuthorizationRequired('Dedicated diagnostic authorization unavailable',
                                                                expected_revision)
    try:
        grant = diagnostic_grants.require_grant(session, asset, expected_revision)
    except diagnostic_grants.DiagnosticAuthorizationError:
        raise alert_diagnostics.DiagnosticAuthorizationRequired('Bound authorization unavailable',
                                                                expected_revision) from None
    observation = _Observation(grant.revision)
    authorize = _deadline_guard(
        alert_diagnostics._authorization_guard(session, asset, grant.revision, guard), deadline)
    try:
        authorize()
        actions, endpoint = _scope(asset, grant)
        token, ca_pem = _credentials(grant, vault)
        authorize()
        transport = DockerTransport(endpoint, ca_pem, token, deadline, authorize)
        raw = transport.containers()
        authorize()
        containers = project_containers(raw)
        truncated = len(containers) > MAX_PROJECTED or len(containers) >= MAX_CONTAINERS
        visible = containers[:MAX_PROJECTED]
        if 'docker.containers' in actions:
            _, budget_limited = _store_containers(observation, visible, len(containers))
            if truncated or budget_limited:
                observation.errors['docker.containers'] = 'evidence_limit' if budget_limited else 'item_limit'
        if 'docker.resources' in actions:
            _resources(transport, containers, observation, authorize)
        authorize()
    except (alert_diagnostics.DiagnosticAuthorizationRequired,
            alert_diagnostics.DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        exc.evidence.update(observation.combined())
        raise
    except diagnostic_grants.DiagnosticAuthorizationError:
        raise alert_diagnostics.DiagnosticAuthorizationRequired(
            'Bound authorization unavailable', grant.revision, observation.combined()) from None
    except DockerTransportError as exc:
        observation.errors['collection'] = exc.code
    return _result(observation)


def _store_containers(observation: _Observation,
                      visible: list[dict[str, object]], observed_count: int) -> tuple[int, bool]:
    metadata = {'items': [], 'truncated': observed_count > len(visible),
                'observed_count': observed_count, 'projected_count': 0,
                'list_limit': MAX_CONTAINERS}
    observation.store('docker.containers', metadata)
    stored_count = 0
    for index in range(len(visible)):
        candidate = {**metadata, 'items': visible[:index + 1],
                     'projected_count': index + 1}
        try:
            observation.store('docker.containers', candidate)
        except DockerTransportError as exc:
            if exc.code != 'evidence_limit':
                raise
            observation.store('docker.containers', {
                **metadata, 'items': visible[:stored_count], 'projected_count': stored_count,
                'truncated': True,
            })
            return stored_count, True
        stored_count += 1
    return stored_count, False


def _resources(transport: DockerTransport, containers: list[dict[str, object]],
               observation: _Observation, authorize: Callable[[], None]) -> None:
    stats: list[dict[str, object]] = []
    truncated = len(containers) > 3 or len(containers) >= MAX_CONTAINERS
    if truncated:
        observation.errors['docker.resources'] = 'item_limit'
    metadata = {'items': [], 'observed_count': len(containers), 'selected_count': 0,
                'sample_limit': 3, 'truncated': truncated}
    observation.store('docker.resources', metadata)
    for container in containers[:3]:
        authorize()
        cid = str(container['id'])
        if not CONTAINER_ID.fullmatch(cid):
            raise DockerTransportError('resource_not_discovered')
        if container['state'] != 'running':
            sample: dict[str, object] = {'id': cid, 'status': 'not_sampled'}
        else:
            try:
                raw_stats = transport.stats(cid)
                authorize()
                projected = project_stats(raw_stats, cid)
                sample = {'id': cid, 'status': 'sampled', 'stats': projected}
                if not projected['metrics_available']:
                    observation.errors['docker.resources:' + cid] = 'invalid_response'
            except DockerTransportError as exc:
                sample = {'id': cid, 'status': 'unavailable', 'error_code': exc.code}
                observation.errors['docker.resources:' + cid] = exc.code
        try:
            observation.store('docker.resources', {
                **metadata, 'items': stats + [sample], 'selected_count': len(stats) + 1,
            })
        except DockerTransportError as exc:
            if exc.code != 'evidence_limit':
                raise
            observation.errors['docker.resources'] = 'evidence_limit'
            observation.store('docker.resources', {
                **metadata, 'items': stats, 'selected_count': len(stats), 'truncated': True,
            })
            return
        stats.append(sample)


def _deadline_guard(authorize: Callable[[], None], deadline: float) -> Callable[[], None]:
    def bounded() -> None:
        authorize()
        if time.monotonic() >= deadline:
            raise DockerTransportError('timeout')
    return bounded


def _result(observation: _Observation) -> dict[str, object]:
    status = 'failed' if not observation.evidence else 'partial' if observation.errors else 'completed'
    summary = {'failed': '只读 Docker 采集失败，需管理员人工排查。',
               'partial': '已保留部分 Docker 只读证据，需人工核实采集完整性。',
               'completed': '已取得授权范围的 Docker 只读证据，根因与恢复仍需人工核实。'}[status]
    result = {'status': status, 'summary': summary, 'evidence': observation.combined()}
    if status == 'failed':
        code = next(iter(observation.errors.values()), 'transport_failed')
        result['error_code'] = code if code in FAILURE_CODES else 'transport_failed'
        result['evidence']['error_code'] = result['error_code']
    return result
