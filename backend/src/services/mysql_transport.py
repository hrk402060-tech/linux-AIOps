import logging
import socket
import ssl
import time
from collections.abc import Callable
from threading import Timer

import pymysql
from pymysql.constants import CLIENT
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)
TOTAL_SECONDS = 30
MAX_WIRE_BYTES = 65536
MAX_ROWS = 16
FAILURE_CODES = frozenset({
    'timeout', 'connection_failed', 'connection_reset', 'authentication_failed',
    'tls_failed', 'credential_decrypt_failed', 'invalid_configuration',
    'invalid_response', 'query_failed', 'unsupported_syntax', 'evidence_limit', 'transport_failed',
})
QUERIES = {
    'status': "SHOW GLOBAL STATUS WHERE Variable_name IN "
              "('Threads_connected','Threads_running','Threads_created','Slow_queries','Uptime')",
    'variables': "SHOW GLOBAL VARIABLES WHERE Variable_name IN "
                 "('version','read_only','super_read_only','innodb_buffer_pool_size','max_connections')",
    'replica': 'SHOW REPLICA STATUS',
    'slave': 'SHOW SLAVE STATUS',
}


class MySQLDiagnosticError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in FAILURE_CODES else 'transport_failed'
        super().__init__(self.code)


def _tls_context(ca_pem: str) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=ca_pem)
        return context
    except (ssl.SSLError, ValueError) as exc:
        raise MySQLDiagnosticError('tls_failed') from exc


class _TLSConnection(pymysql.connections.Connection):
    check: Callable[[], None]
    received: int = 0

    def _request_authentication(self) -> None:
        self.check()
        # PyMySQL otherwise authenticates without TLS when CLIENT_SSL is absent.
        if not self.ssl or not self.server_capabilities & CLIENT.SSL:
            raise MySQLDiagnosticError('tls_failed')
        super()._request_authentication()

    def _read_bytes(self, num_bytes: int) -> bytes:
        self.check()
        if self.received + num_bytes > MAX_WIRE_BYTES:
            raise MySQLDiagnosticError('evidence_limit')
        self.received += num_bytes
        return super()._read_bytes(num_bytes)

    def _write_bytes(self, data: bytes) -> None:
        self.check()
        super()._write_bytes(data)


def failure_code(exc: BaseException) -> str:
    if isinstance(exc, MySQLDiagnosticError):
        return exc.code
    if isinstance(exc, ssl.SSLError):
        return 'tls_failed'
    if isinstance(exc, TimeoutError):
        return 'timeout'
    code = exc.args[0] if exc.args else None
    if code in {1044, 1045, 1698}:
        return 'authentication_failed'
    if code == 2026 or any(isinstance(cause, ssl.SSLError) for cause in (
        exc.__cause__, exc.__context__, getattr(exc, 'original_exception', None),
    )):
        return 'tls_failed'
    if code == 1064:
        return 'unsupported_syntax'
    if code in {2002, 2003}:
        return 'connection_failed'
    if code in {2006, 2013}:
        return 'connection_reset'
    return 'query_failed' if isinstance(exc, pymysql.MySQLError) else 'transport_failed'


class MySQLTransport:
    def __init__(
        self, host: str, port: int, username: str, password: str, ca_pem: str, database: str,
        deadline: float, authorize: Callable[[], None],
    ) -> None:
        self.deadline, self.authorize = deadline, authorize
        self.authorize()
        self.connection = _TLSConnection(
            host=host, port=port, user=username, password=password, database=database or None,
            ssl=_tls_context(ca_pem), connect_timeout=5, read_timeout=5, write_timeout=5,
            cursorclass=DictCursor, autocommit=None, local_infile=False, defer_connect=True,
        )
        self.connection.check = self._bound_timeout
        self.watchdog = Timer(max(0.01, deadline - time.monotonic()), self._abort)
        self.watchdog.daemon = True
        self.watchdog.start()
        try:
            self._bound_timeout()
            self.connection.connect()
            if not self.connection._secure or not isinstance(self.connection._sock, ssl.SSLSocket):
                raise MySQLDiagnosticError('tls_failed')
            self.authorize()
            self._bound_timeout()
        except (pymysql.MySQLError, OSError, MySQLDiagnosticError) as exc:
            self.close()
            raise MySQLDiagnosticError(self._failure_code(exc)) from exc
        except BaseException:
            self.close()
            raise

    def _bound_timeout(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MySQLDiagnosticError('timeout')
        self.connection._read_timeout = self.connection._write_timeout = min(5, remaining)
        sock = getattr(self.connection, '_sock', None)
        if sock is not None:
            sock.settimeout(min(5, remaining))

    def _failure_code(self, exc: BaseException) -> str:
        return 'timeout' if time.monotonic() >= self.deadline else failure_code(exc)

    def request(self, action: str) -> list[dict[str, object]]:
        if action not in QUERIES:
            raise MySQLDiagnosticError('invalid_configuration')
        self.authorize()
        self._bound_timeout()
        try:
            cursor = self.connection.cursor()
            try:
                cursor.execute(QUERIES[action])
                rows = cursor.fetchall()
            finally:
                cursor.close()
        except (pymysql.MySQLError, OSError) as exc:
            raise MySQLDiagnosticError(self._failure_code(exc)) from exc
        self.authorize()
        self._bound_timeout()
        if (not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS
                or any(not isinstance(row, dict) for row in rows)):
            raise MySQLDiagnosticError('invalid_response')
        return list(rows)

    def _abort(self) -> None:
        sock = getattr(self.connection, '_sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.debug('MySQL diagnostic socket already closed')
        self.connection._force_close()

    def close(self) -> None:
        self.watchdog.cancel()
        self._abort()
