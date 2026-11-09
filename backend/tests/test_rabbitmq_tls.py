import base64
import socket
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from src.services.rabbitmq_transport import RabbitMQDiagnosticError, RabbitMQTransport
from test_kubernetes_grants import certificate
from test_mysql_tls import _credentials


@contextmanager
def _peer(
    context: ssl.SSLContext | None, response_delay: float = 0,
) -> Iterator[tuple[int, list[bytes]]]:
    received: list[bytes] = []
    errors: list[str] = []
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(4)
    port = listener.getsockname()[1]

    def serve() -> None:
        try:
            with listener:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(3)
                    if context is None:
                        received.append(raw.recv(4096))
                        raw.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n')
                        return
                    try:
                        with context.wrap_socket(raw, server_side=True) as secured:
                            request = b''
                            while b'\r\n\r\n' not in request and len(request) < 8192:
                                chunk = secured.recv(4096)
                                if not chunk:
                                    break
                                request += chunk
                            received.append(request)
                            Event().wait(response_delay)
                            secured.sendall(
                                b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                                b'Content-Length: 2\r\nConnection: close\r\n\r\n{}',
                            )
                    except (ssl.SSLError, ConnectionError):
                        received.append(b'tls-rejected')
        except OSError as exc:
            errors.append(type(exc).__name__)

    worker = Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield port, received
    finally:
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert not errors, errors


def test_rabbitmq_valid_ca_and_ip_authenticates_only_after_verified_tls(tmp_path: Path) -> None:
    ca, context = _credentials(tmp_path, '127.0.0.1')
    with _peer(context) as (port, received):
        transport = RabbitMQTransport(
            '127.0.0.1', port, 'reader', 'sensitive-password', ca, '/',
            time.monotonic() + 3, Mock(),
        )
        try:
            assert transport.request('status') == {}
        finally:
            transport.close()
    assert received[0].startswith(b'GET /api/overview HTTP/1.1\r\n')
    token = base64.b64encode(b'reader:sensitive-password')
    assert b'Authorization: Basic ' + token in received[0]


@pytest.mark.parametrize('mismatch', ['ca', 'ip'])
def test_rabbitmq_tls_mismatch_never_sends_authentication(tmp_path: Path, mismatch: str) -> None:
    ca, context = _credentials(tmp_path, '192.0.2.1' if mismatch == 'ip' else '127.0.0.1')
    with _peer(context) as (port, received):
        transport = RabbitMQTransport(
            '127.0.0.1', port, 'reader', 'sensitive-password',
            certificate() if mismatch == 'ca' else ca, '/', time.monotonic() + 3, Mock(),
        )
        try:
            with pytest.raises(RabbitMQDiagnosticError, match='tls_failed'):
                transport.request('status')
        finally:
            transport.close()
    assert received == [b'tls-rejected']


def test_rabbitmq_plain_http_server_never_receives_authorization() -> None:
    with _peer(None) as (port, received):
        transport = RabbitMQTransport(
            '127.0.0.1', port, 'reader', 'sensitive-password', certificate(), '/',
            time.monotonic() + 3, Mock(),
        )
        try:
            with pytest.raises(RabbitMQDiagnosticError, match='tls_failed'):
                transport.request('status')
        finally:
            transport.close()
    assert received
    assert b'Authorization' not in received[0]
    assert b'GET /api/' not in received[0]
    assert b'sensitive-password' not in received[0]


def test_rabbitmq_deadline_interrupts_blocked_real_tls_request(tmp_path: Path) -> None:
    ca, context = _credentials(tmp_path, '127.0.0.1')
    with _peer(context, response_delay=1.5) as (port, received):
        started = time.monotonic()
        transport = RabbitMQTransport(
            '127.0.0.1', port, 'reader', 'sensitive-password', ca, '/',
            started + 0.4, Mock(),
        )
        try:
            with pytest.raises(RabbitMQDiagnosticError, match='timeout'):
                transport.request('status')
            assert time.monotonic() - started < 1.2
        finally:
            transport.close()
    assert received[0].startswith(b'GET /api/overview HTTP/1.1\r\n')
