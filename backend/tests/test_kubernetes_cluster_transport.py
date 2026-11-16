import http.client
import io
import socket
import ssl
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.services import kubernetes_cluster_transport as transport
from src.services.kubernetes_kubeconfig import ParsedKubeconfig


@pytest.fixture(autouse=True)
def _explicit_test_target_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, kubernetes_api_allowed_cidrs="192.0.2.0/24")
    monkeypatch.setattr(transport, "get_settings", MagicMock(return_value=settings))


def _parsed() -> ParsedKubeconfig:
    return ParsedKubeconfig(
        "https://api.example.test:6443", "selected", "token", "reader",
        None, "private-test-token", None, None, None, None,
    )


class _Response:
    def __init__(self, body: bytes = b"{}", status: int = 200,
                 headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = headers or {}

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read1(self, size: int) -> bytes:
        return self.body.read(size)


def _install_connection(monkeypatch: pytest.MonkeyPatch, response: _Response) -> MagicMock:
    context = MagicMock()
    connection = MagicMock()
    connection.getresponse.return_value = response
    connection.peer_certificate = b""
    monkeypatch.setattr(transport.ssl, "create_default_context", MagicMock(return_value=context))
    monkeypatch.setattr(transport, "_resolve_addresses", MagicMock(return_value=["192.0.2.20"]))
    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", MagicMock(return_value=connection))
    return connection


@pytest.mark.parametrize(
    "status,code",
    [(301, "redirect_rejected"), (302, "redirect_rejected"), (307, "redirect_rejected"),
     (401, "upstream_unauthorized"), (403, "upstream_forbidden"), (404, "api_unavailable"),
     (429, "upstream_throttled"), (500, "upstream_unavailable"),
     (503, "upstream_unavailable"), (204, "unexpected_status"), (201, "unexpected_status")],
)
def test_upstream_statuses_fail_closed(status: int, code: str) -> None:
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport._check_status(status)
    assert error.value.code == code


def test_only_reviews_accept_created_status() -> None:
    transport._check_status(200)
    transport._check_status(200, review=True)
    transport._check_status(201, review=True)


@pytest.mark.parametrize(
    "path,payload",
    [("/api/v1/pods", None), ("/api/v1/secrets", None), ("/version?x=1", None),
     ("/apis/authentication.k8s.io/v1/selfsubjectreviews", None), ("/version", {}),
     ("/api/v1/namespaces/default/pods/p/exec", {})],
)
def test_fixed_path_allowlist_rejects_before_tls(
    monkeypatch: pytest.MonkeyPatch, path: str, payload: dict[str, object] | None,
) -> None:
    create_context = MagicMock(side_effect=AssertionError("must not create TLS context"))
    monkeypatch.setattr(transport.ssl, "create_default_context", create_context)
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.request_json(_parsed(), path, transport.RequestBudget(), payload)
    assert error.value.code == "path_rejected"
    create_context.assert_not_called()


def test_path_allowlist_precedes_unconfigured_target_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services.kubernetes_cluster_target_policy import KubernetesTargetPolicy

    create_context = MagicMock(side_effect=AssertionError("must not create TLS context"))
    monkeypatch.setattr(transport.ssl, "create_default_context", create_context)
    budget = transport.RequestBudget(policy=KubernetesTargetPolicy())
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.request_json(_parsed(), "/api/v1/secrets", budget)
    assert error.value.code == "path_rejected"
    create_context.assert_not_called()


def test_mixed_dns_answers_rejected_before_any_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    future: Future[list[tuple[object, ...]]] = Future()
    future.set_result([
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", 6443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 6443)),
    ])
    pool = MagicMock()
    pool.submit.return_value = future
    connection = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(transport, "_DNS_POOL", pool)
    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", connection)
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.request_json(_parsed(), "/version", transport.RequestBudget())
    assert error.value.code == "target_rejected"
    connection.assert_not_called()


def test_numeric_connection_preserves_hostname_and_skips_second_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, secured, context = MagicMock(), MagicMock(), MagicMock()
    secured.getpeercert.return_value = b"peer-cert"
    context.wrap_socket.return_value = secured
    socket_factory = MagicMock(return_value=raw)
    resolve_again = MagicMock(side_effect=AssertionError("second DNS lookup is forbidden"))
    monkeypatch.setattr(transport.socket, "socket", socket_factory)
    monkeypatch.setattr(transport.socket, "getaddrinfo", resolve_again)
    connection = transport._PinnedHTTPSConnection(
        "api.example.test", 6443, "192.0.2.20", transport.RequestBudget(), context,
    )
    connection.connect()
    socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
    raw.connect.assert_called_once_with(("192.0.2.20", 6443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname="api.example.test")
    assert connection.peer_certificate == b"peer-cert"
    resolve_again.assert_not_called()
    connection.close()
    secured.close.assert_called_once()


@pytest.mark.parametrize("body", [b"[]", b"null", b'"private-test-token"', b"{broken"])
def test_json_objects_only_and_safe_errors(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    connection = _install_connection(monkeypatch, _Response(body))
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.request_json(_parsed(), "/version", transport.RequestBudget())
    assert error.value.code == "invalid_response"
    assert "private-test-token" not in str(error.value)
    assert body.decode() not in str(error.value)
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    "headers,body,code",
    [({"Content-Length": "invalid"}, b"{}", "invalid_response"),
     ({"Content-Length": "-1"}, b"{}", "response_too_large"),
     ({"Content-Length": "65537"}, b"{}", "response_too_large"),
     ({"Content-Encoding": "gzip"}, b"{}", "invalid_response"),
     ({"Content-Length": "100"}, b"{}", "invalid_response"),
     ({}, b"x" * (transport.MAX_RESPONSE_BYTES + 1), "response_too_large")],
)
def test_response_length_encoding_and_body_limits(
    headers: dict[str, str], body: bytes, code: str,
) -> None:
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport._read_body(_Response(body, headers=headers), transport.RequestBudget())
    assert error.value.code == code


def test_shared_budget_limits_total_response_bytes_and_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = transport.RequestBudget()
    budget.charge(transport.MAX_TOTAL_BYTES)
    with pytest.raises(transport.KubernetesConnectionError) as error:
        budget.charge(1)
    assert error.value.code == "response_too_large"
    monkeypatch.setattr(transport.time, "monotonic", MagicMock(return_value=31.0))
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.RequestBudget(deadline=30.0).remaining()
    assert error.value.code == "timeout"


def test_raw_receive_enforces_deadline_during_slow_response(monkeypatch: pytest.MonkeyPatch) -> None:
    secured = MagicMock()
    secured.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\n")
    clock = MagicMock(side_effect=[0.0, 2.0])
    monkeypatch.setattr(transport.time, "monotonic", clock)
    reader = transport._DeadlineReader(secured, transport.RequestBudget(deadline=1.0))
    try:
        with pytest.raises(transport.KubernetesConnectionError) as error:
            reader.readinto(bytearray(1))
        assert error.value.code == "timeout"
        secured.settimeout.assert_called_once_with(1.0)
    finally:
        reader.close()


def test_raw_receive_charges_header_bytes() -> None:
    secured = MagicMock()
    secured.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    budget = transport.RequestBudget()
    response = http.client.HTTPResponse(transport._DeadlineSocket(secured, budget))
    try:
        response.begin()
        assert response.read() == b"{}"
        assert budget.received_bytes > len(b"{}")
    finally:
        response.close()


def test_review_posts_only_with_json_and_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _install_connection(monkeypatch, _Response(b'{"status":{}}', status=201))
    payload = {"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview"}
    path = "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews"
    result, _ = transport.request_json(_parsed(), path, transport.RequestBudget(), payload)
    assert result == {"status": {}}
    args, kwargs = connection.request.call_args
    assert args == ("POST", path)
    assert kwargs["headers"]["Authorization"] == "Bearer private-test-token"
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert kwargs["headers"]["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    "exception,code",
    [(ssl.SSLError("private-test-token"), "tls_error"),
     (OSError("private-test-token"), "network_error"),
     (socket.timeout("private-test-token"), "timeout")],
)
def test_transport_failures_hide_external_details(
    monkeypatch: pytest.MonkeyPatch, exception: Exception, code: str,
) -> None:
    connection = _install_connection(monkeypatch, _Response())
    connection.request.side_effect = exception
    with pytest.raises(transport.KubernetesConnectionError) as error:
        transport.request_json(_parsed(), "/version", transport.RequestBudget())
    assert error.value.code == code
    assert "private-test-token" not in str(error.value)
    connection.close.assert_called_once()
