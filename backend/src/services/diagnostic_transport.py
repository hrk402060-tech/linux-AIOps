import errno
import json
import time
import ipaddress
import socket
from threading import Timer
from typing import Callable

from cryptography.exceptions import InvalidTag
import paramiko

from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.services.crypto import CredentialCryptoError, CredentialVault
from src.services.ssh_connection import parse_private_key

PROTOCOL = 'linuxops-readonly-v1'
MAX_BYTES = 32768
MAX_ERROR_BYTES = 4096
TOTAL_SECONDS = 30
TRANSIENT_CODES = frozenset({'timeout', 'connection_reset', 'connection_failed'})
FAILURE_CODES = TRANSIENT_CODES | frozenset({'transport_failed', 'ssh_protocol_failed',
    'authentication_failed', 'private_key_invalid', 'credential_decrypt_failed',
    'host_key_mismatch', 'invalid_action', 'connection_closed', 'output_limit',
    'gateway_failed', 'invalid_gateway_response', 'restricted_gateway_required'})


class DiagnosticTransportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in FAILURE_CODES else 'transport_failed'
        self.retryable = self.code in TRANSIENT_CODES
        super().__init__(self.code)


def failure_code(exc: BaseException) -> str:
    if isinstance(exc, DiagnosticTransportError):
        return exc.code
    if isinstance(exc, (CredentialCryptoError, InvalidTag)):
        return 'credential_decrypt_failed'
    if isinstance(exc, paramiko.PasswordRequiredException):
        return 'private_key_invalid'
    if isinstance(exc, paramiko.AuthenticationException):
        return 'authentication_failed'
    if isinstance(exc, paramiko.BadHostKeyException):
        return 'host_key_mismatch'
    if isinstance(exc, TimeoutError):
        return 'timeout'
    if isinstance(exc, OSError):
        if exc.errno in {errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE}:
            return 'connection_reset'
        if exc.errno in {errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH}:
            return 'connection_failed'
        if exc.errno == errno.ETIMEDOUT:
            return 'timeout'
    return 'ssh_protocol_failed' if isinstance(exc, paramiko.SSHException) else 'transport_failed'


def _remaining(deadline: float, authorize: Callable[[], None] | None = None) -> float:
    if authorize is not None:
        authorize()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DiagnosticTransportError('timeout')
    return remaining


def _private_key(grant: DiagnosticGrant, vault: CredentialVault) -> paramiko.PKey:
    try:
        secret = vault.decrypt(grant.encrypted_secret, grant.nonce, grant.id,
                               'diagnostic_private_key', grant.key_version)
    except (CredentialCryptoError, InvalidTag, ValueError) as exc:
        raise DiagnosticTransportError('credential_decrypt_failed') from exc
    try:
        return parse_private_key(secret)
    except (ValueError, paramiko.SSHException) as exc:
        raise DiagnosticTransportError('private_key_invalid') from exc


def _connect(
    asset: Asset, grant: DiagnosticGrant, vault: CredentialVault,
    expected_key_or_deadline: object, deadline: float | Callable[[], None] | None = None,
    authorize: Callable[[], None] | None = None,
) -> paramiko.SSHClient:
    actual_deadline = float(expected_key_or_deadline) if isinstance(
        expected_key_or_deadline, (int, float)
    ) else float(deadline) if isinstance(deadline, (int, float)) else 0.0
    actual_authorize = (
        deadline if callable(deadline) else authorize
    )
    _remaining(actual_deadline, actual_authorize)
    key = _private_key(grant, vault)
    _remaining(actual_deadline, actual_authorize)
    client = paramiko.SSHClient()
    address = ipaddress.ip_address(grant.host)
    connection = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
    def cancel() -> None:
        connection.close()
        client.close()
    watchdog = Timer(max(0.01, actual_deadline - time.monotonic()), cancel)
    watchdog.daemon = True
    watchdog.start()
    try:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        remaining = min(6, _remaining(actual_deadline, actual_authorize))
        connection.settimeout(remaining)
        connection.connect((str(address), grant.port))
        remaining = min(6, _remaining(actual_deadline, actual_authorize))
        client.connect(hostname=grant.host, port=grant.port, username=grant.username, pkey=key,
                       timeout=remaining, banner_timeout=remaining, auth_timeout=remaining,
                       allow_agent=False, look_for_keys=False, sock=connection)
        _remaining(actual_deadline, actual_authorize)
        return client
    except Exception:
        connection.close()
        client.close()
        raise
    finally:
        watchdog.cancel()


def request(client: paramiko.SSHClient, action: str, deadline: float,
            authorize: Callable[[], None]) -> dict[str, object]:
    if action != 'handshake':
        from src.services.readonly_diagnostics import ACTION_BY_ID
        if action not in ACTION_BY_ID or ACTION_BY_ID[action].domain != 'linux' or not ACTION_BY_ID[action].available:
            raise DiagnosticTransportError('invalid_action')
    remaining = _remaining(deadline, authorize)
    transport = client.get_transport()
    if transport is None:
        raise DiagnosticTransportError('connection_closed')
    channel = transport.open_session(timeout=min(remaining, 5))
    watchdog = Timer(max(0.01, deadline - time.monotonic()), channel.close)
    watchdog.daemon = True
    watchdog.start()
    try:
        remaining = _remaining(deadline, authorize)
        channel.settimeout(min(remaining, 5))
        channel.exec_command(f'{PROTOCOL}:{action}')
        return _read_response(channel, action, deadline, authorize)
    finally:
        channel.close()
        watchdog.cancel()


def _read_response(channel: paramiko.Channel, action: str, deadline: float,
                   authorize: Callable[[], None]) -> dict[str, object]:
    output, errors = bytearray(), bytearray()
    while True:
        _remaining(deadline, authorize)
        if channel.recv_ready():
            _remaining(deadline, authorize)
            output.extend(channel.recv(4096))
        if channel.recv_stderr_ready():
            _remaining(deadline, authorize)
            errors.extend(channel.recv_stderr(1024))
        if len(output) > MAX_BYTES or len(errors) > MAX_ERROR_BYTES:
            raise DiagnosticTransportError('output_limit')
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        time.sleep(0.05)
    _remaining(deadline, authorize)
    if channel.recv_exit_status() != 0 or errors:
        raise DiagnosticTransportError('gateway_failed')
    try:
        body = json.loads(output)
    except (ValueError, UnicodeError) as exc:
        raise DiagnosticTransportError('invalid_gateway_response') from exc
    if not isinstance(body, dict) or body.get('protocol') != PROTOCOL or body.get('action') != action:
        raise DiagnosticTransportError('invalid_gateway_response')
    return body


def handshake(client: paramiko.SSHClient, grant: DiagnosticGrant, deadline: float,
              authorize: Callable[[], None]) -> None:
    body = request(client, 'handshake', deadline, authorize)
    uid = body.get('uid')
    if (type(uid) is not int or uid <= 0 or body.get('username') != grant.username
        or body.get('writes_allowed') is not False or body.get('status') != 'completed'):
        raise DiagnosticTransportError('restricted_gateway_required')
