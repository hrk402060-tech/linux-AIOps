import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.services import alert_diagnostics, diagnostic_grants, redis_grants
from src.services.crypto import CredentialVault
from src.services.redis_transport import (
    FAILURE_CODES as FAILURE_CODES,
    TOTAL_SECONDS, RedisDiagnosticError, RedisTransport,
)

POLICY_VERSION = 'readonly-redis-2026.10.22'
MAX_EVIDENCE_CHARS = 12000


@dataclass
class _Observation:
    revision: int
    evidence: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def store(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        remaining = sum(len(item) for name, item in self.evidence.items() if name != key)
        if len(encoded) + remaining > MAX_EVIDENCE_CHARS - 2048:
            raise RedisDiagnosticError('evidence_limit')
        self.evidence[key] = encoded

    def combined(self) -> dict[str, str]:
        return {'collection_mode': 'restricted_read_only_redis', 'read_only': 'true',
                'remediation': 'disabled', 'grant_revision': str(self.revision),
                'policy_version': POLICY_VERSION,
                'action_errors': json.dumps(self.errors, ensure_ascii=True, separators=(',', ':')),
                **self.evidence}


def _pairs(value: object) -> dict[str, str]:
    if not isinstance(value, str):
        raise RedisDiagnosticError('invalid_response')
    result: dict[str, str] = {}
    for line in value.splitlines():
        if not line or line.startswith('#') or ':' not in line:
            continue
        key, item = line.split(':', 1)
        if re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key):
            result[key] = item[:256]
    return result


def _number(values: dict[str, str], key: str) -> int:
    value = values.get(key)
    if value is None or not re.fullmatch(r'[0-9]{1,20}', value):
        raise RedisDiagnosticError('invalid_response')
    return int(value)


def _status(transport: RedisTransport, observation: _Observation) -> None:
    values = _pairs(transport.request('status'))
    observation.store('redis.status', {
        key: values[key] for key in ('redis_version', 'uptime_in_seconds', 'role') if key in values
    })


def _memory(transport: RedisTransport, observation: _Observation) -> None:
    values = _pairs(transport.request('memory'))
    fields = ('used_memory', 'used_memory_peak', 'maxmemory', 'mem_fragmentation_ratio')
    result: dict[str, object] = {}
    for key in fields:
        if key in values:
            result[key] = float(values[key]) if key == 'mem_fragmentation_ratio' else _number(values, key)
    if not result:
        raise RedisDiagnosticError('invalid_response')
    observation.store('redis.memory', result)


def _clients(transport: RedisTransport, observation: _Observation) -> None:
    values = _pairs(transport.request('clients'))
    observation.store('redis.clients', {
        key: _number(values, key) for key in ('connected_clients', 'blocked_clients')
        if key in values
    })


def _slowlog(transport: RedisTransport, observation: _Observation) -> None:
    raw = transport.request('slowlog')
    if not isinstance(raw, list):
        raise RedisDiagnosticError('invalid_response')
    durations: list[int] = []
    ids: list[int] = []
    for item in raw:
        if not isinstance(item, list) or len(item) < 3 or not all(isinstance(value, int) for value in item[:3]):
            raise RedisDiagnosticError('invalid_response')
        ids.append(item[0])
        durations.append(item[2])
    observation.store('redis.slowlog', {
        'count': len(raw), 'latest_id': max(ids) if ids else None,
        'max_duration_microseconds': max(durations) if durations else 0,
    })


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
    transport: RedisTransport | None = None
    try:
        authorize()
        password, ca_pem = redis_grants.load_secret(grant, vault)
        transport = RedisTransport(grant.host, grant.port, grant.username, password, ca_pem,
                                   int((grant.redis_config or {}).get('database', 0)),
                                   time.monotonic() + TOTAL_SECONDS, authorize)
        for action in grant.action_ids:
            authorize()
            try:
                if action == 'redis.status':
                    _status(transport, observation)
                elif action == 'redis.memory':
                    _memory(transport, observation)
                elif action == 'redis.clients':
                    _clients(transport, observation)
                else:
                    _slowlog(transport, observation)
            except RedisDiagnosticError as exc:
                observation.errors[action] = exc.code
                if exc.code in {'timeout', 'connection_reset', 'connection_failed', 'evidence_limit'}:
                    break
        authorize()
    except (alert_diagnostics.DiagnosticAuthorizationRequired,
            alert_diagnostics.DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        exc.evidence.update(observation.combined())
        raise
    except RedisDiagnosticError as exc:
        observation.errors['collection'] = exc.code
    except ValueError:
        observation.errors['collection'] = 'credential_decrypt_failed'
    finally:
        if transport is not None:
            transport.close()
    status = 'failed' if not observation.evidence else 'partial' if observation.errors else 'completed'
    result: dict[str, object] = {
        'status': status,
        'summary': {'failed': '只读 Redis 采集失败，需管理员人工排查。',
                    'partial': '已保留部分 Redis 只读证据，需人工核实采集完整性。',
                    'completed': '已取得授权范围的 Redis 只读证据，根因与恢复仍需人工核实。'}[status],
        'evidence': observation.combined(),
    }
    if status == 'failed':
        result['error_code'] = next(iter(observation.errors.values()), 'transport_failed')
    return result
