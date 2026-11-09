import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.models.asset import Asset
from src.services import alert_diagnostics, diagnostic_grants, mysql_grants
from src.services.crypto import CredentialVault
from src.services.mysql_transport import (
    FAILURE_CODES, TOTAL_SECONDS, MySQLDiagnosticError, MySQLTransport,
)

POLICY_VERSION = 'readonly-mysql-2026.10.22'
MAX_EVIDENCE_CHARS = 12000
STATUS_FIELDS = frozenset({'Threads_connected', 'Threads_running', 'Threads_created', 'Slow_queries', 'Uptime'})
VARIABLE_FIELDS = frozenset({'version', 'read_only', 'super_read_only', 'innodb_buffer_pool_size', 'max_connections'})


@dataclass
class _Observation:
    revision: int
    evidence: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def store(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        remaining = sum(len(item) for name, item in self.evidence.items() if name != key)
        if len(encoded) + remaining > MAX_EVIDENCE_CHARS - 2048:
            raise MySQLDiagnosticError('evidence_limit')
        self.evidence[key] = encoded

    def combined(self) -> dict[str, str]:
        return {
            'collection_mode': 'restricted_read_only_mysql', 'read_only': 'true',
            'remediation': 'disabled', 'grant_revision': str(self.revision),
            'policy_version': POLICY_VERSION,
            'action_errors': json.dumps(self.errors, ensure_ascii=True, separators=(',', ':')),
            **self.evidence,
        }


def _counter(value: object) -> int:
    text = str(value)
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,20}', text) or int(text) > 2**64 - 1:
        raise MySQLDiagnosticError('invalid_response')
    return int(text)


def _project_status(rows: list[dict[str, object]], allowed: frozenset[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for row in rows:
        name, value = row.get('Variable_name'), row.get('Value')
        if not isinstance(name, str) or name not in allowed:
            continue
        if name in result:
            raise MySQLDiagnosticError('invalid_response')
        if name == 'version':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9][A-Za-z0-9.+_-]{0,79}', value):
                raise MySQLDiagnosticError('invalid_response')
            result[name] = value
        elif name in {'read_only', 'super_read_only'}:
            if value not in {'ON', 'OFF', '0', '1'}:
                raise MySQLDiagnosticError('invalid_response')
            result[name] = value in {'ON', '1'}
        else:
            result[name] = _counter(value)
    if not result:
        raise MySQLDiagnosticError('invalid_response')
    return result


def _status(transport: MySQLTransport, observation: _Observation) -> None:
    status = _project_status(transport.request('status'), STATUS_FIELDS)
    variables = _project_status(transport.request('variables'), VARIABLE_FIELDS)
    observation.store('mysql.status', {'status': status, 'variables': variables})


def _replica_row(row: dict[str, object]) -> dict[str, object]:
    projected: dict[str, object] = {}
    for canonical, legacy in (
        ('Replica_IO_Running', 'Slave_IO_Running'), ('Replica_SQL_Running', 'Slave_SQL_Running'),
    ):
        value = row.get(canonical, row.get(legacy))
        if value not in {'Yes', 'No', 'Connecting'}:
            raise MySQLDiagnosticError('invalid_response')
        projected[canonical] = value
    lag = row.get('Seconds_Behind_Source', row.get('Seconds_Behind_Master'))
    projected['Seconds_Behind_Source'] = None if lag is None else _counter(lag)
    for name in ('Last_IO_Errno', 'Last_SQL_Errno'):
        projected[name] = _counter(row.get(name, 0))
    return projected


def _replication(transport: MySQLTransport, observation: _Observation) -> None:
    try:
        rows = transport.request('replica')
    except MySQLDiagnosticError as exc:
        if exc.code != 'unsupported_syntax':
            raise
        rows = transport.request('slave')
    observation.store('mysql.replication', {
        'configured': bool(rows), 'channels': [_replica_row(row) for row in rows],
        'observed_count': len(rows),
    })


def _observe(
    transport: MySQLTransport, actions: list[str], observation: _Observation, authorize: Callable[[], None],
) -> None:
    for action in actions:
        authorize()
        try:
            if action == 'mysql.status':
                _status(transport, observation)
            else:
                _replication(transport, observation)
        except MySQLDiagnosticError as exc:
            observation.errors[action] = exc.code
            if exc.code in {'timeout', 'connection_reset', 'evidence_limit', 'transport_failed'}:
                break
    authorize()


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
    observation, transport = _Observation(grant.revision), None
    authorize = alert_diagnostics._authorization_guard(session, asset, grant.revision, guard)
    deadline = time.monotonic() + TOTAL_SECONDS
    try:
        authorize()
        password, ca_pem = mysql_grants.load_secret(grant, vault)
        config = grant.mysql_config
        transport = MySQLTransport(grant.host, grant.port, grant.username, password, ca_pem,
                                   str(config.get('database', '')), deadline, authorize)
        _observe(transport, grant.action_ids, observation, authorize)
    except (alert_diagnostics.DiagnosticAuthorizationRequired,
            alert_diagnostics.DiagnosticReadInterrupted) as exc:
        exc.revision = grant.revision
        exc.evidence.update(observation.combined())
        raise
    except MySQLDiagnosticError as exc:
        observation.errors['collection'] = exc.code
    except ValueError:
        observation.errors['collection'] = 'credential_decrypt_failed'
    finally:
        if transport is not None:
            transport.close()
    return _result(observation)


def _result(observation: _Observation) -> dict[str, object]:
    status = 'failed' if not observation.evidence else 'partial' if observation.errors else 'completed'
    summary = {
        'failed': '只读 MySQL 采集失败，需管理员人工排查。',
        'partial': '已保留部分 MySQL 只读证据，需人工核实采集完整性。',
        'completed': '已取得授权范围的 MySQL 只读证据，根因与恢复仍需人工核实。',
    }[status]
    result: dict[str, object] = {'status': status, 'summary': summary, 'evidence': observation.combined()}
    if status == 'failed':
        code = next(iter(observation.errors.values()), 'transport_failed')
        result['error_code'] = code if code in FAILURE_CODES else 'transport_failed'
    return result
