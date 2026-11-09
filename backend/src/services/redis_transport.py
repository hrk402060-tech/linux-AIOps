import logging
import socket
import ssl
import time
from collections.abc import Callable
from threading import Timer

logger = logging.getLogger(__name__)
TOTAL_SECONDS = 30
MAX_WIRE_BYTES = 65536
MAX_ARRAY_ITEMS = 64
FAILURE_CODES = frozenset({
    'timeout', 'connection_failed', 'connection_reset', 'authentication_failed',
    'tls_failed', 'credential_decrypt_failed', 'invalid_configuration',
    'invalid_response', 'unsupported_command', 'evidence_limit', 'transport_failed',
})


class RedisDiagnosticError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in FAILURE_CODES else 'transport_failed'
        super().__init__(self.code)


def _tls_context(ca_pem: str) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.load_verify_locations(cadata=ca_pem)
        return context
    except (ssl.SSLError, ValueError) as exc:
        raise RedisDiagnosticError('tls_failed') from exc


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, RedisDiagnosticError):
        return exc.code
    if isinstance(exc, ssl.SSLError):
        return 'tls_failed'
    if isinstance(exc, TimeoutError):
        return 'timeout'
    if isinstance(exc, ConnectionResetError):
        return 'connection_reset'
    if isinstance(exc, OSError):
        return 'connection_failed'
    return 'transport_failed'


class RedisTransport:
    def __init__(
        self, host: str, port: int, username: str, password: str, ca_pem: str,
        database: int, deadline: float, authorize: Callable[[], None],
    ) -> None:
        self.host, self.deadline, self.authorize = host, deadline, authorize
        self.socket: ssl.SSLSocket | None = None
        self.received = 0
        self.watchdog = Timer(max(0.01, deadline - time.monotonic()), self._abort)
        self.watchdog.daemon = True
        self.watchdog.start()
        try:
            self.authorize()
            raw = socket.create_connection((host, port), timeout=5)
            self.socket = _tls_context(ca_pem).wrap_socket(raw, server_hostname=host)
            self._bound_timeout()
            self._command('AUTH', username, password, expect='OK')
            if database:
                self._command('SELECT', str(database), expect='OK')
        except (RedisDiagnosticError, OSError, ssl.SSLError) as exc:
            code = exc.code if isinstance(exc, RedisDiagnosticError) else _failure_code(exc)
            self.close()
            raise RedisDiagnosticError(code) from exc
        except BaseException:
            self.close()
            raise

    def _bound_timeout(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RedisDiagnosticError('timeout')
        if self.socket is not None:
            self.socket.settimeout(min(5, remaining))
        self.authorize()

    def _write(self, value: bytes) -> None:
        self._bound_timeout()
        if self.socket is None:
            raise RedisDiagnosticError('connection_reset')
        self.socket.sendall(value)

    def _read(self, size: int) -> bytes:
        self._bound_timeout()
        if self.socket is None:
            raise RedisDiagnosticError('connection_reset')
        if self.received + size > MAX_WIRE_BYTES:
            raise RedisDiagnosticError('evidence_limit')
        data = self.socket.recv(size)
        if not data:
            raise RedisDiagnosticError('connection_reset')
        self.received += len(data)
        return data

    def _line(self) -> bytes:
        value = bytearray()
        while len(value) <= 4096:
            chunk = self._read(1)
            value.extend(chunk)
            if value.endswith(b'\r\n'):
                return bytes(value[:-2])
        raise RedisDiagnosticError('invalid_response')

    def _reply(self) -> object:
        prefix = self._read(1)
        if prefix == b'+':
            return self._line().decode('utf-8', 'replace')
        if prefix == b'-':
            message = self._line().decode('utf-8', 'replace')
            if message.startswith('NOAUTH') or message.startswith('WRONGPASS'):
                raise RedisDiagnosticError('authentication_failed')
            if 'unknown command' in message.lower():
                raise RedisDiagnosticError('unsupported_command')
            raise RedisDiagnosticError('invalid_response')
        if prefix == b':':
            value = self._line()
            if not value.lstrip(b'-').isdigit():
                raise RedisDiagnosticError('invalid_response')
            return int(value)
        if prefix == b'$':
            length = self._line()
            if not length.lstrip(b'-').isdigit():
                raise RedisDiagnosticError('invalid_response')
            size = int(length)
            if size == -1:
                return None
            if size < 0 or size > MAX_WIRE_BYTES:
                raise RedisDiagnosticError('evidence_limit')
            data = self._read(size + 2)
            if not data.endswith(b'\r\n'):
                raise RedisDiagnosticError('invalid_response')
            return data[:-2].decode('utf-8', 'replace')
        if prefix == b'*':
            count = self._line()
            if not count.isdigit() or int(count) > MAX_ARRAY_ITEMS:
                raise RedisDiagnosticError('invalid_response')
            return [self._reply() for _ in range(int(count))]
        raise RedisDiagnosticError('invalid_response')

    def _command(self, *parts: str, expect: str | None = None) -> object:
        encoded = [f'*{len(parts)}\r\n'.encode()]
        for part in parts:
            data = part.encode('utf-8')
            if len(data) > 4096 or b'\r' in data or b'\n' in data:
                raise RedisDiagnosticError('invalid_configuration')
            encoded.extend((f'${len(data)}\r\n'.encode(), data, b'\r\n'))
        try:
            self._write(b''.join(encoded))
            result = self._reply()
        except (RedisDiagnosticError, OSError, ssl.SSLError) as exc:
            if isinstance(exc, RedisDiagnosticError):
                raise
            raise RedisDiagnosticError(_failure_code(exc)) from exc
        if expect is not None and result != expect:
            raise RedisDiagnosticError('invalid_response')
        return result

    def request(self, action: str) -> object:
        commands = {
            'status': ('INFO', 'server'),
            'memory': ('INFO', 'memory'),
            'clients': ('INFO', 'clients'),
            'slowlog': ('SLOWLOG', 'GET', '16'),
        }
        command = commands.get(action)
        if command is None:
            raise RedisDiagnosticError('invalid_configuration')
        self.received = 0
        return self._command(*command)

    def _abort(self) -> None:
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.socket.close()
            except OSError:
                pass

    def close(self) -> None:
        self.watchdog.cancel()
        self._abort()
