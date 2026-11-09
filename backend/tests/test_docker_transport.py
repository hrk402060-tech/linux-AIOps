import ipaddress
import json
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.services import docker_transport as transport


CONTAINER_ID = "a" * 64
UNKNOWN_ID = "b" * 64


def response(body: object, status: int = 200) -> Mock:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Content-Encoding": "identity"}
    reply = Mock(status=status, read=Mock(side_effect=[
        raw[index:index + 8192] for index in range(0, len(raw), 8192)
    ] + [b""]))
    reply.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    return reply


def container(identifier: str = CONTAINER_ID) -> dict[str, object]:
    return {"Id": identifier, "Names": ["/web"], "State": "running", "Ports": []}


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch) -> Mock:
    context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True)
    monkeypatch.setattr(transport.ssl, "create_default_context", Mock(return_value=context))
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", Mock())
    return context


def make_transport(context: Mock, connection: Mock, authorize: Mock | None = None,
                   deadline: float | None = None) -> transport.DockerTransport:
    with patch.object(transport.ssl, "create_default_context", return_value=context):
        transport.http.client.HTTPSConnection.return_value = connection
        client = transport.DockerTransport(
            "https://192.0.2.20:2376",
            "ca",
            "opaque-token",
            deadline if deadline is not None else time.monotonic() + 40,
            authorize or Mock(),
        )
    return client


def test_transport_enforces_tls_context_and_exact_get_path(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([])
    authorize = Mock()
    with patch.object(transport.ssl, "create_default_context", return_value=context) as tls:
        with patch.object(transport.http.client, "HTTPSConnection", return_value=connection) as connect:
            client = transport.DockerTransport(
                "https://192.0.2.20:2376", "ca", "opaque-token",
                time.monotonic() + 40, authorize,
            )
            assert client.containers() == []
    tls.assert_called_once_with(cadata="ca")
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert connect.call_args.args == ("192.0.2.20", 2376)
    assert 0 < connect.call_args.kwargs["timeout"] <= transport.REQUEST_SECONDS
    path, kwargs = connection.request.call_args
    assert path == ("GET", "/v1.45/containers/json?all=true&limit=50")
    assert kwargs["headers"]["Authorization"] == "Bearer opaque-token"
    assert kwargs["headers"]["Accept"] == "application/json"
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    connection.close.assert_called_once()


@pytest.mark.parametrize("endpoint", [
    "http://192.0.2.20:2376",
    "https://docker.example:2376",
    "https://127.0.0.1:2376",
    "https://169.254.169.254:2376",
    "https://[::1]:2376",
    "https://[::ffff:192.0.2.20]:2376",
    "https://192.0.2.20:2376/arbitrary",
    "https://user:password@192.0.2.20:2376",
])
def test_invalid_endpoint_never_connects(endpoint: str, context: Mock) -> None:
    with patch.object(transport.http.client, "HTTPSConnection") as connect:
        with pytest.raises(transport.DockerTransportError, match="invalid_configuration"):
            transport.DockerTransport(endpoint, "ca", "opaque-token",
                                      time.monotonic() + 40, Mock())
    connect.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/v1.45/containers/exec",
        "/v1.45/containers/" + CONTAINER_ID + "/exec",
        "/v1.45/containers/" + CONTAINER_ID + "/stats?stream=false",
        "/v1.45/containers/json?all=false&limit=50",
        "/v1.45/containers/json?all=true&limit=51",
    ],
)
def test_transport_rejects_non_pinned_paths_before_connect(path: str, context: Mock) -> None:
    with patch.object(transport.http.client, "HTTPSConnection") as connect:
        client = make_transport(context, Mock())
        with pytest.raises(transport.DockerTransportError, match="invalid_action"):
            client.request(path)
    connect.assert_not_called()


def test_stats_requires_id_from_same_container_snapshot(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([container()])
    client = make_transport(context, connection)
    assert client.containers()[0]["Id"] == CONTAINER_ID
    connection.getresponse.return_value = response({
        "id": CONTAINER_ID,
        "memory_stats": {"usage": 1, "limit": 2},
        "cpu_stats": {"cpu_usage": {}},
        "networks": {},
    })
    assert client.stats(CONTAINER_ID)["id"] == CONTAINER_ID
    with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
        client.stats(UNKNOWN_ID)
    assert connection.request.call_count == 2


def test_stats_before_discovery_never_opens_connection(context: Mock) -> None:
    connection = Mock()
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
        client.stats(CONTAINER_ID)
    connection.request.assert_not_called()


def test_direct_stats_path_cannot_bypass_discovery(context: Mock) -> None:
    connection = Mock()
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
        client.request(f"/v1.45/containers/{CONTAINER_ID}/stats?stream=false&one-shot=true")
    connection.request.assert_not_called()


def test_failed_refresh_discards_previous_container_snapshot(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([container()])
    client = make_transport(context, connection)
    client.containers()
    connection.getresponse.return_value = response(b"invalid-json")
    with pytest.raises(transport.DockerTransportError, match="invalid_response"):
        client.containers()
    with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
        client.stats(CONTAINER_ID)
    assert connection.request.call_count == 2


def test_stopped_container_not_eligible_for_stats(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([{**container(), "State": "exited"}])
    client = make_transport(context, connection)
    client.containers()
    with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
        client.stats(CONTAINER_ID)
    assert connection.request.call_count == 1


@pytest.mark.parametrize("returned_id", [UNKNOWN_ID, None])
def test_stats_response_requires_matching_id(context: Mock, returned_id: str | None) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([container()])
    client = make_transport(context, connection)
    client.containers()
    connection.getresponse.return_value = response({"id": returned_id})
    with pytest.raises(transport.DockerTransportError, match="invalid_response"):
        client.stats(CONTAINER_ID)


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "http_redirect"),
        (302, "http_redirect"),
        (307, "http_redirect"),
        (308, "http_redirect"),
        (401, "http_unauthorized"),
        (403, "http_forbidden"),
        (404, "http_not_found"),
        (429, "http_throttled"),
        (500, "http_server_error"),
        (503, "http_server_error"),
    ],
)
def test_http_errors_do_not_follow_or_read_sensitive_body(
    status: int, code: str, context: Mock,
) -> None:
    connection = Mock()
    reply = response(b"Authorization: Bearer poison-token", status)
    connection.getresponse.return_value = reply
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError, match=code) as error:
        client.request("/v1.45/containers/json?all=true&limit=50")
    assert "poison-token" not in str(error.value)
    reply.read.assert_not_called()
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_wrong_ca_or_tls_identity_fails_closed_before_http(context: Mock) -> None:
    with patch.object(transport.ssl, "create_default_context",
                      side_effect=ssl.SSLError("wrong ca")):
        with pytest.raises(transport.DockerTransportError, match="invalid_credentials"):
            transport.DockerTransport(
                "https://192.0.2.20:2376", "wrong-ca", "opaque-token",
                time.monotonic() + 40, Mock(),
            )
    with patch.object(transport.ssl, "create_default_context", return_value=context):
        client = transport.DockerTransport(
            "https://192.0.2.20:2376", "ca", "opaque-token",
            time.monotonic() + 40, Mock(),
        )
    assert client.context is context
    assert context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED


def test_expired_deadline_never_opens_connection(context: Mock) -> None:
    with patch.object(transport.http.client, "HTTPSConnection") as connect:
        client = make_transport(context, Mock(), deadline=time.monotonic() - 1)
        with pytest.raises(transport.DockerTransportError, match="timeout"):
            client.request("/v1.45/containers/json?all=true&limit=50")
    connect.assert_not_called()


def test_guard_cancellation_closes_connection(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([])
    guard = Mock(side_effect=[None, RuntimeError("lease-lost")])
    client = make_transport(context, connection, guard)
    with pytest.raises(RuntimeError, match="lease-lost"):
        client.request("/v1.45/containers/json?all=true&limit=50")
    connection.close.assert_called_once()


@pytest.mark.parametrize("exception,code", [
    (TimeoutError("poison-token"), "timeout"),
    (ssl.SSLError("poison-token"), "tls_failed"),
    (ConnectionResetError("poison-token"), "connection_reset"),
    (OSError("poison-token"), "connection_failed"),
])
def test_native_errors_map_to_fixed_codes_and_close(
    exception: Exception, code: str, context: Mock,
) -> None:
    connection = Mock()
    connection.getresponse.side_effect = exception
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError, match=code) as error:
        client.containers()
    assert "poison-token" not in str(error.value)
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"not-json", "invalid_response"),
        (b"x" * (transport.MAX_RESPONSE_BYTES + 1), "response_limit"),
    ],
)
def test_chunked_body_is_json_and_bounded(
    raw: bytes, code: str, context: Mock,
) -> None:
    connection = Mock()
    reply = response(raw)
    connection.getresponse.return_value = reply
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError, match=code):
        client.request("/v1.45/containers/json?all=true&limit=50")
    connection.close.assert_called_once()


@pytest.mark.parametrize("header,value", [
    ("Content-Type", "text/html"),
    ("Content-Type", "application/octet-stream"),
    ("Content-Encoding", "gzip"),
])
def test_non_json_or_encoded_body_is_rejected_without_echoing(
    header: str, value: str, context: Mock,
) -> None:
    connection = Mock()
    reply = response(b"poison-token")
    headers = {"Content-Type": "application/json", "Content-Encoding": "identity", header: value}
    reply.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    connection.getresponse.return_value = reply
    client = make_transport(context, connection)
    with pytest.raises(transport.DockerTransportError) as error:
        client.containers()
    assert "poison-token" not in str(error.value)
    reply.read.assert_not_called()
    connection.close.assert_called_once()


def test_total_byte_budget_spans_requests(context: Mock) -> None:
    connection = Mock()
    connection.getresponse.return_value = response([])
    client = make_transport(context, connection)
    client.bytes_read = transport.MAX_TOTAL_BYTES
    with pytest.raises(transport.DockerTransportError, match="total_byte_limit"):
        client.request("/v1.45/containers/json?all=true&limit=50")
    connection.close.assert_called_once()


@pytest.mark.parametrize("detached", [False, True])
def test_watchdog_shuts_down_retained_socket_without_error_text(
    detached: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    active = Mock()
    active.shutdown.side_effect = OSError("poison-token")
    connection = Mock(sock=None if detached else active)
    transport._abort(connection, [active])
    active.shutdown.assert_called_once_with(transport.socket.SHUT_RDWR)
    connection.close.assert_called_once()
    assert "Docker deadline socket shutdown failed" in caplog.text
    assert "poison-token" not in caplog.text


@pytest.mark.parametrize("token", ["", "has space", "\x00bad", "\u00e9"])
def test_token_is_ascii_and_nonempty_before_connection(context: Mock, token: str) -> None:
    with patch.object(transport.http.client, "HTTPSConnection") as connect:
        with patch.object(transport.ssl, "create_default_context", return_value=context):
            with pytest.raises(transport.DockerTransportError, match="invalid_credentials"):
                transport.DockerTransport(
                    "https://192.0.2.20:2376", "ca", token,
                    time.monotonic() + 40, Mock(),
                )
    connect.assert_not_called()


@contextmanager
def https_server(tmp_path: Path, san_ip: str = "192.0.2.20"
                 ) -> Iterator[tuple[int, str, list[tuple[str, str]]]]:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Docker test CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Docker fixture")])
    certificate = (
        x509.CertificateBuilder().subject_name(server_name).issuer_name(ca_name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san_ip))]),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.crt", tmp_path / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    calls: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            calls.append((self.command, self.path))
            body = [container()] if self.path == "/v1.45/containers/json?all=true&limit=50" else {
                "id": CONTAINER_ID, "memory_stats": {"usage": 1, "limit": 10},
                "cpu_stats": {"cpu_usage": {"total_usage": 3}}, "networks": {},
            }
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, ca.public_bytes(serialization.Encoding.PEM).decode(), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def route_to_fixture(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    original = transport.socket.create_connection

    def local_connection(address: tuple[str, int], *args: object, **kwargs: object) -> object:
        assert address == ("192.0.2.20", port)
        return original(("127.0.0.1", port), *args, **kwargs)

    monkeypatch.setattr(transport.socket, "create_connection", local_connection)


def test_real_https_uses_ip_san_and_scoped_gets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with https_server(tmp_path) as (port, ca, calls):
        route_to_fixture(monkeypatch, port)
        client = transport.DockerTransport(
            f"https://192.0.2.20:{port}", ca, "fixture-token",
            time.monotonic() + 40, Mock(),
        )
        assert client.containers()[0]["Id"] == CONTAINER_ID
        assert client.stats(CONTAINER_ID)["id"] == CONTAINER_ID
        with pytest.raises(transport.DockerTransportError, match="resource_not_discovered"):
            client.stats(UNKNOWN_ID)
    assert calls == [
        ("GET", "/v1.45/containers/json?all=true&limit=50"),
        ("GET", "/v1.45/containers/" + CONTAINER_ID + "/stats?stream=false&one-shot=true"),
    ]


@pytest.mark.parametrize("trust", ["wrong_san", "wrong_ca"])
def test_real_https_rejects_wrong_identity_or_untrusted_ca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trust: str,
) -> None:
    from test_kubernetes_grants import certificate

    with https_server(tmp_path, "192.0.2.21" if trust == "wrong_san" else "192.0.2.20") as (
        port, ca, calls,
    ):
        route_to_fixture(monkeypatch, port)
        client = transport.DockerTransport(
            f"https://192.0.2.20:{port}", certificate() if trust == "wrong_ca" else ca,
            "fixture-token", time.monotonic() + 40, Mock(),
        )
        with pytest.raises(transport.DockerTransportError, match="tls_failed") as error:
            client.containers()
        assert "fixture-token" not in str(error.value)
        assert calls == []
