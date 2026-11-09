import base64
import http.client
import json
import socket
import ssl
import time
from collections.abc import Callable
from threading import Timer
from urllib.parse import quote

TOTAL_SECONDS = 30
MAX_WIRE_BYTES = 128 * 1024
FAILURE_CODES = frozenset({
    'timeout', 'connection_failed', 'connection_reset', 'authentication_failed',
    'tls_failed', 'credential_decrypt_failed', 'invalid_configuration',
    'invalid_response', 'evidence_limit', 'transport_failed',
})


class RabbitMQDiagnosticError(RuntimeError):
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
        raise RabbitMQDiagnosticError('tls_failed') from exc


class RabbitMQTransport:
    def __init__(
        self, host: str, port: int, username: str, password: str, ca_pem: str,
        vhost: str, deadline: float, authorize: Callable[[], None],
    ) -> None:
        self.host, self.port, self.username, self.password = host, port, username, password
        self.vhost, self.deadline, self.authorize = vhost, deadline, authorize
        self.context = _tls_context(ca_pem)
        self.connection: http.client.HTTPSConnection | None = None
        self.received = 0
        self.watchdog = Timer(max(0.01, deadline - time.monotonic()), self._abort)
        self.watchdog.daemon = True
        self.watchdog.start()

    def _abort(self) -> None:
        connection = self.connection
        if connection is not None and connection.sock is not None:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                connection.close()

    def request(self, action: str) -> object:
        paths = {
            'status': '/api/overview',
            'nodes': '/api/nodes',
            'queues': f'/api/queues/{quote(self.vhost, safe="")}',
            'consumers': f'/api/consumers/{quote(self.vhost, safe="")}',
        }
        path = paths.get(action)
        if path is None:
            raise RabbitMQDiagnosticError('invalid_configuration')
        self.authorize()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RabbitMQDiagnosticError('timeout')
        token = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
        connection = http.client.HTTPSConnection(
            self.host, self.port, context=self.context, timeout=min(5, remaining))
        self.connection = connection
        try:
            connection.connect()
            self.authorize()
            self._check_deadline()
            connection.request('GET', path, headers={
                'Authorization': f'Basic {token}', 'Accept': 'application/json',
                'Connection': 'close',
            })
            response = connection.getresponse()
            if response.status in {401, 403}:
                raise RabbitMQDiagnosticError('authentication_failed')
            if response.status != 200:
                raise RabbitMQDiagnosticError('invalid_response')
            chunks: list[bytes] = []
            while True:
                self.authorize()
                self._check_deadline()
                chunk = response.read(min(4096, MAX_WIRE_BYTES - self.received + 1))
                self.received += len(chunk)
                if self.received > MAX_WIRE_BYTES:
                    raise RabbitMQDiagnosticError('evidence_limit')
                if not chunk:
                    break
                chunks.append(chunk)
            result = json.loads(b''.join(chunks))
        except (TimeoutError, socket.timeout) as exc:
            raise RabbitMQDiagnosticError('timeout') from exc
        except ssl.SSLError as exc:
            self._check_deadline()
            raise RabbitMQDiagnosticError('tls_failed') from exc
        except (ValueError, RecursionError, http.client.HTTPException) as exc:
            self._check_deadline()
            raise RabbitMQDiagnosticError('invalid_response') from exc
        except OSError as exc:
            self._check_deadline()
            raise RabbitMQDiagnosticError('connection_failed') from exc
        finally:
            connection.close()
            self.connection = None
        self._check_deadline()
        self.authorize()
        return result

    def _check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise RabbitMQDiagnosticError('timeout')

    def close(self) -> None:
        self.watchdog.cancel()
        self._abort()
        if self.connection is not None:
            self.connection.close()
