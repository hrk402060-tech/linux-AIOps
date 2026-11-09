import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.services import alert_diagnostics, diagnostic_grants, rabbitmq_grants
from src.services.crypto import CredentialVault
from src.services.rabbitmq_transport import (
    FAILURE_CODES as FAILURE_CODES,
    TOTAL_SECONDS, RabbitMQDiagnosticError, RabbitMQTransport,
)
from src.services.terminal_assistant import redact_context

POLICY_VERSION = 'readonly-rabbitmq-2026.10.22'
MAX_EVIDENCE_CHARS = 12000


@dataclass
class _Observation:
    revision: int
    evidence: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def store(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
        remaining = sum(len(item) for name, item in self.evidence.items() if name != key)
        if len(encoded) + remaining > MAX_EVIDENCE_CHARS - 2048:
            raise RabbitMQDiagnosticError('evidence_limit')
        self.evidence[key] = encoded

    def combined(self) -> dict[str, str]:
        return {'collection_mode': 'restricted_read_only_rabbitmq', 'read_only': 'true',
                'remediation': 'disabled', 'grant_revision': str(self.revision),
                'policy_version': POLICY_VERSION,
                'action_errors': json.dumps(self.errors, ensure_ascii=True, separators=(',', ':')),
                **self.evidence}


def _project(raw: object, fields: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise RabbitMQDiagnosticError('invalid_response')
    result: dict[str, object] = {}
    for key in fields:
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = redact_context(value[:120])
        elif type(value) is int and 0 <= value <= 10 ** 20:
            result[key] = value
        elif type(value) is float and math.isfinite(value) and 0 <= value <= 10 ** 20:
            result[key] = value
        elif isinstance(value, bool):
            result[key] = value
    return result


def _status(transport: RabbitMQTransport, observation: _Observation) -> None:
    result = _project(transport.request('status'), ('rabbitmq_version', 'cluster_name'))
    if not result:
        raise RabbitMQDiagnosticError('invalid_response')
    observation.store('rabbitmq.status', result)


def _items(raw: object, fields: tuple[str, ...], limit: int) -> dict[str, object]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise RabbitMQDiagnosticError('invalid_response')
    ordered = sorted(raw, key=lambda item: str(item.get('name', '')))
    return {'items': [_project(item, fields) for item in ordered[:limit]], 'truncated': len(raw) > limit}


def _nodes(transport: RabbitMQTransport, observation: _Observation) -> None:
    observation.store('rabbitmq.nodes', _items(transport.request('nodes'), (
        'name', 'running', 'mem_used', 'mem_limit', 'fd_used', 'fd_total',
        'sockets_used', 'sockets_total',
    ), 20))


def _queues(transport: RabbitMQTransport, observation: _Observation) -> None:
    observation.store('rabbitmq.queues', _items(transport.request('queues'), (
        'name', 'messages', 'messages_ready', 'messages_unacknowledged', 'consumers', 'state',
    ), 50))


def _consumers(transport: RabbitMQTransport, observation: _Observation) -> None:
    raw = transport.request('consumers')
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise RabbitMQDiagnosticError('invalid_response')
    observation.store('rabbitmq.consumers', {'count': len(raw)})


def _read_actions(
    transport: RabbitMQTransport, action_ids: list[str], authorize: Callable[[], None],
    observation: _Observation,
) -> None:
    actions = {'rabbitmq.status': _status, 'rabbitmq.nodes': _nodes,
               'rabbitmq.queues': _queues, 'rabbitmq.consumers': _consumers}
    for action in action_ids:
        authorize()
        try:
            actions[action](transport, observation)
        except RabbitMQDiagnosticError as exc:
            observation.errors[action] = exc.code
            if exc.code in {'timeout', 'connection_reset', 'connection_failed', 'evidence_limit',
                            'tls_failed', 'authentication_failed'}:
                break


def collect(
    asset: Asset, vault: CredentialVault, session: Session | None,
    guard: Callable[[], None] | None = None, expected_revision: int | None = None,
) -> dict[str, object]:
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
    authorize = alert_diagnostics._authorization_guard(session, asset, grant.revision, guard)
    transport: RabbitMQTransport | None = None
    try:
        authorize()
        password, ca_pem = rabbitmq_grants.load_secret(grant, vault)
        transport = RabbitMQTransport(
            grant.host, grant.port, grant.username, password, ca_pem,
            str((grant.rabbitmq_config or {}).get('vhost', '/')),
            time.monotonic() + TOTAL_SECONDS, authorize)
        _read_actions(transport, grant.action_ids, authorize, observation)
        authorize()
    except (alert_diagnostics.DiagnosticAuthorizationRequired,
            alert_diagnostics.DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        exc.evidence.update(observation.combined())
        raise
    except RabbitMQDiagnosticError as exc:
        observation.errors['collection'] = exc.code
    except ValueError:
        observation.errors['collection'] = 'credential_decrypt_failed'
    finally:
        if transport is not None:
            transport.close()
    return _result(observation)


def _result(observation: _Observation) -> dict[str, object]:
    status = 'failed' if not observation.evidence else 'partial' if observation.errors else 'completed'
    result: dict[str, object] = {
        'status': status,
        'summary': {'failed': '只读 RabbitMQ 采集失败，需管理员人工排查。',
                    'partial': '已保留部分 RabbitMQ 只读证据，需人工核实采集完整性。',
                    'completed': '已取得授权范围的 RabbitMQ 只读证据，根因与恢复仍需人工核实。'}[status],
        'evidence': observation.combined(),
    }
    if status == 'failed':
        result['error_code'] = next(iter(observation.errors.values()), 'transport_failed')
    return result
