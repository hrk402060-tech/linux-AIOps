import http.client
import json
import logging
import re
import socket
import ssl
import time
from collections.abc import Callable
from threading import Timer
from urllib.parse import urlencode

from src.schemas.kubernetes_grant import endpoint_target as validate_endpoint_target

TOTAL_SECONDS = 40
REQUEST_SECONDS = 5
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)
NAMESPACE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
RESOURCE_KINDS = {'pods': 'Pod', 'events': 'Event', 'deployments': 'Deployment',
                  'statefulsets': 'StatefulSet', 'daemonsets': 'DaemonSet'}
FAILURE_CODES = frozenset({
    'invalid_configuration', 'invalid_credentials', 'credential_decrypt_failed',
    'invalid_action', 'timeout', 'tls_failed', 'connection_failed', 'connection_reset', 'transport_failed',
    'http_redirect', 'http_unauthorized', 'http_forbidden', 'http_not_found',
    'resource_expired', 'http_throttled', 'http_server_error', 'http_error',
    'response_limit', 'total_byte_limit', 'invalid_response', 'page_limit',
    'item_limit', 'evidence_limit', 'pagination_loop',
})


class KubernetesTransportError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in FAILURE_CODES else 'transport_failed'
        super().__init__(self.code)


def endpoint_target(endpoint: str) -> tuple[str, int]:
    try:
        return validate_endpoint_target(endpoint)
    except ValueError:
        raise KubernetesTransportError('invalid_configuration') from None


def _abort(connection: http.client.HTTPSConnection, sockets: list[socket.socket]) -> None:
    active = connection.sock if connection.sock is not None else next(iter(sockets), None)
    try:
        if active is not None:
            active.shutdown(socket.SHUT_RDWR)
    except OSError:
        logger.warning('Kubernetes deadline socket shutdown failed')
    finally:
        try:
            connection.close()
        except OSError:
            logger.warning('Kubernetes deadline connection close failed')


def _status_code(status: int) -> str:
    if 300 <= status < 400:
        return 'http_redirect'
    return {401: 'http_unauthorized', 403: 'http_forbidden', 404: 'http_not_found',
            410: 'resource_expired', 429: 'http_throttled'}.get(
                status, 'http_server_error' if status >= 500 else 'http_error')


class KubernetesTransport:
    def __init__(self, endpoint: str, ca_pem: str, token: str, deadline: float,
                 authorize: Callable[[], None]) -> None:
        self.host, self.port = endpoint_target(endpoint)
        if (not isinstance(token, str) or not 1 <= len(token) <= 32768
            or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)):
            raise KubernetesTransportError('invalid_credentials')
        try:
            self.context = ssl.create_default_context(cadata=ca_pem)
            self.context.check_hostname = True
            self.context.verify_mode = ssl.CERT_REQUIRED
        except (ssl.SSLError, ValueError, TypeError):
            raise KubernetesTransportError('invalid_credentials') from None
        self.token, self.deadline, self.authorize = token, deadline, authorize
        self.bytes_read = 0

    def _timeout(self, request_deadline: float) -> float:
        self.authorize()
        remaining = min(self.deadline, request_deadline) - time.monotonic()
        if remaining <= 0:
            raise KubernetesTransportError('timeout')
        return min(REQUEST_SECONDS, remaining)

    def _set_timeout(self, connection: http.client.HTTPSConnection,
                     request_deadline: float) -> None:
        connection.timeout = self._timeout(request_deadline)
        if connection.sock is not None:
            connection.sock.settimeout(connection.timeout)

    def request(self, namespace: str, resource: str, continuation: str = '') -> dict[str, object]:
        if (not isinstance(namespace, str) or not NAMESPACE.fullmatch(namespace)
            or resource not in RESOURCE_KINDS or not isinstance(continuation, str)
            or len(continuation) > 2048):
            raise KubernetesTransportError('invalid_action')
        prefix = '/api/v1' if resource in {'pods', 'events'} else '/apis/apps/v1'
        query = {'limit': '50', **({'continue': continuation} if continuation else {})}
        path = f'{prefix}/namespaces/{namespace}/{resource}?' + urlencode(query)
        request_deadline = min(self.deadline, time.monotonic() + REQUEST_SECONDS)
        connection = http.client.HTTPSConnection(self.host, self.port, context=self.context,
            timeout=self._timeout(request_deadline))
        sockets: list[socket.socket] = []
        watchdog = Timer(max(0.001, request_deadline - time.monotonic()),
                         _abort, args=(connection, sockets))
        watchdog.daemon = True
        watchdog.start()
        try:
            self._set_timeout(connection, request_deadline)
            connection.request('GET', path, headers={
                'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json',
                'Accept-Encoding': 'identity',
            })
            if connection.sock is not None:
                # getresponse may detach a closing socket while its makefile still owns it.
                sockets.append(connection.sock)
            self._set_timeout(connection, request_deadline)
            response = connection.getresponse()
            self._timeout(request_deadline)
            if not 200 <= response.status < 300:
                raise KubernetesTransportError(_status_code(response.status))
            return self._read(connection, response, request_deadline)
        except ssl.SSLError:
            raise KubernetesTransportError('tls_failed') from None
        except TimeoutError:
            raise KubernetesTransportError('timeout') from None
        except ConnectionResetError:
            raise KubernetesTransportError('connection_reset') from None
        except OSError:
            raise KubernetesTransportError('connection_failed') from None
        except (http.client.HTTPException, ValueError):
            raise KubernetesTransportError('transport_failed') from None
        finally:
            watchdog.cancel()
            connection.close()

    def _read(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse,
              request_deadline: float) -> dict[str, object]:
        output = bytearray()
        while True:
            self._set_timeout(connection, request_deadline)
            block = response.read(8192)
            self._timeout(request_deadline)
            if not block:
                break
            self.bytes_read += len(block)
            output.extend(block)
            if len(output) > MAX_RESPONSE_BYTES:
                raise KubernetesTransportError('response_limit')
            if self.bytes_read > MAX_TOTAL_BYTES:
                raise KubernetesTransportError('total_byte_limit')
        try:
            body = json.loads(output)
        except (ValueError, UnicodeError, RecursionError):
            raise KubernetesTransportError('invalid_response') from None
        if not isinstance(body, dict):
            raise KubernetesTransportError('invalid_response')
        return body
