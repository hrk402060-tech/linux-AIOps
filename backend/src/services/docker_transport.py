import http.client
import json
import logging
import re
import socket
import ssl
import time
from collections.abc import Callable
from threading import Timer

from src.schemas.docker_grant import endpoint_target

TOTAL_SECONDS = 40
REQUEST_SECONDS = 5
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_CONTAINERS = 50
CONTAINER_ID = re.compile(r'^[0-9a-f]{64}$')
FAILURE_CODES = frozenset({
    'invalid_configuration', 'invalid_credentials', 'credential_decrypt_failed',
    'invalid_action', 'timeout', 'tls_failed', 'connection_failed', 'connection_reset',
    'transport_failed', 'http_redirect', 'http_unauthorized', 'http_forbidden',
    'http_not_found', 'http_throttled', 'http_server_error', 'http_error',
    'response_limit', 'total_byte_limit', 'invalid_response', 'item_limit',
    'evidence_limit', 'resource_not_discovered',
})
logger = logging.getLogger(__name__)


class DockerTransportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in FAILURE_CODES else 'transport_failed'
        super().__init__(self.code)


def _abort(connection: http.client.HTTPSConnection, sockets: list[socket.socket]) -> None:
    active = connection.sock if connection.sock is not None else next(iter(sockets), None)
    try:
        if active is not None:
            active.shutdown(socket.SHUT_RDWR)
    except OSError:
        logger.warning('Docker deadline socket shutdown failed')
    finally:
        try:
            connection.close()
        except OSError:
            logger.warning('Docker deadline connection close failed')


def _status_code(status: int) -> str:
    if 300 <= status < 400:
        return 'http_redirect'
    return {401: 'http_unauthorized', 403: 'http_forbidden', 404: 'http_not_found',
            429: 'http_throttled'}.get(status, 'http_server_error' if status >= 500 else 'http_error')


class DockerTransport:
    def __init__(self, endpoint: str, ca_pem: str, token: str, deadline: float,
                 authorize: Callable[[], None]) -> None:
        try:
            self.host, self.port = endpoint_target(endpoint)
        except (ValueError, TypeError):
            raise DockerTransportError('invalid_configuration') from None
        self.discovered_ids: set[str] = set()
        if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)):
            raise DockerTransportError('invalid_credentials')
        try:
            self.context = ssl.create_default_context(cadata=ca_pem)
            self.context.check_hostname = True
            self.context.verify_mode = ssl.CERT_REQUIRED
        except (ssl.SSLError, ValueError, TypeError):
            raise DockerTransportError('invalid_credentials') from None
        self.token, self.deadline, self.authorize, self.bytes_read = token, deadline, authorize, 0

    def _timeout(self, request_deadline: float) -> float:
        self.authorize()
        remaining = min(self.deadline, request_deadline) - time.monotonic()
        if remaining <= 0:
            raise DockerTransportError('timeout')
        return min(REQUEST_SECONDS, remaining)

    def request(self, path: str) -> object:
        container_id = path.split('/')[3] if path.startswith('/v1.45/containers/') else ''
        allowed_stats = CONTAINER_ID.fullmatch(container_id)
        valid_list = path == '/v1.45/containers/json?all=true&limit=50'
        valid_stats = bool(allowed_stats) and path == (
            f'/v1.45/containers/{container_id}/stats?stream=false&one-shot=true')
        if not (valid_list or valid_stats):
            raise DockerTransportError('invalid_action')
        if valid_stats and container_id not in self.discovered_ids:
            raise DockerTransportError('resource_not_discovered')
        request_deadline = min(self.deadline, time.monotonic() + REQUEST_SECONDS)
        connection = http.client.HTTPSConnection(self.host, self.port, context=self.context,
                                                  timeout=self._timeout(request_deadline))
        sockets: list[socket.socket] = []
        watchdog = Timer(max(0.001, request_deadline - time.monotonic()), _abort,
                         args=(connection, sockets))
        watchdog.daemon = True
        watchdog.start()
        try:
            connection.request('GET', path, headers={
                'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json',
                'Accept-Encoding': 'identity',
            })
            if connection.sock is not None:
                sockets.append(connection.sock)
            response = connection.getresponse()
            self._timeout(request_deadline)
            if not 200 <= response.status < 300:
                raise DockerTransportError(_status_code(response.status))
            content_type_header = response.getheader('Content-Type', '')
            encoding_header = response.getheader('Content-Encoding', 'identity')
            if not isinstance(content_type_header, str) or not isinstance(encoding_header, str):
                raise DockerTransportError('invalid_response')
            content_type = content_type_header.split(';', 1)[0].strip().lower()
            encoding = encoding_header.strip().lower()
            if content_type != 'application/json' or encoding != 'identity':
                raise DockerTransportError('invalid_response')
            return self._read(connection, response, request_deadline)
        except ssl.SSLError:
            raise DockerTransportError('tls_failed') from None
        except TimeoutError:
            raise DockerTransportError('timeout') from None
        except ConnectionResetError:
            raise DockerTransportError('connection_reset') from None
        except OSError:
            raise DockerTransportError('connection_failed') from None
        except (http.client.HTTPException, ValueError):
            raise DockerTransportError('transport_failed') from None
        finally:
            watchdog.cancel()
            connection.close()

    def containers(self) -> list[object]:
        self.discovered_ids.clear()
        body = self.request('/v1.45/containers/json?all=true&limit=50')
        if not isinstance(body, list) or len(body) > MAX_CONTAINERS:
            raise DockerTransportError('invalid_response')
        identifiers: set[str] = set()
        for item in body:
            if (not isinstance(item, dict) or not isinstance(item.get('Id'), str)
                    or not CONTAINER_ID.fullmatch(item['Id']) or item['Id'] in identifiers):
                raise DockerTransportError('invalid_response')
            identifiers.add(item['Id'])
        self.discovered_ids = {item['Id'] for item in body if item.get('State') == 'running'}
        return body

    def stats(self, container_id: str) -> dict[str, object]:
        if not CONTAINER_ID.fullmatch(container_id) or container_id not in self.discovered_ids:
            raise DockerTransportError('resource_not_discovered')
        body = self.request('/v1.45/containers/' + container_id + '/stats?stream=false&one-shot=true')
        if not isinstance(body, dict) or body.get('id') != container_id:
            raise DockerTransportError('invalid_response')
        return body

    def _read(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse,
              request_deadline: float) -> object:
        output = bytearray()
        while True:
            timeout = self._timeout(request_deadline)
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            block = response.read(8192)
            if not block:
                break
            self.bytes_read += len(block)
            output.extend(block)
            if len(output) > MAX_RESPONSE_BYTES:
                raise DockerTransportError('response_limit')
            if self.bytes_read > MAX_TOTAL_BYTES:
                raise DockerTransportError('total_byte_limit')
        try:
            return json.loads(output)
        except (ValueError, UnicodeError, RecursionError):
            raise DockerTransportError('invalid_response') from None
