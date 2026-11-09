import json
import ssl
import time
from unittest.mock import MagicMock

import pytest

from src.services import rabbitmq_transport
from src.services.rabbitmq_transport import RabbitMQDiagnosticError, RabbitMQTransport


def _transport(
    monkeypatch: pytest.MonkeyPatch, status: int = 200, body: bytes = b'{}',
) -> tuple[RabbitMQTransport, MagicMock, MagicMock]:
    connection = MagicMock()
    connection.getresponse.return_value.status = status
    connection.getresponse.return_value.read.side_effect = [body, b'']
    monkeypatch.setattr(rabbitmq_transport.http.client, 'HTTPSConnection', MagicMock(return_value=connection))
    monkeypatch.setattr(rabbitmq_transport, '_tls_context', MagicMock(return_value=ssl.create_default_context()))
    authorize = MagicMock()
    transport = RabbitMQTransport(
        '192.0.2.10', 15671, 'readonly', 'transport-password', 'ca', '/prod/team',
        time.monotonic() + 30, authorize,
    )
    return transport, connection, authorize


@pytest.mark.parametrize(('action', 'path'), [
    ('status', '/api/overview'), ('nodes', '/api/nodes'),
    ('queues', '/api/queues/%2Fprod%2Fteam'), ('consumers', '/api/consumers/%2Fprod%2Fteam'),
])
def test_rabbitmq_transport_uses_only_fixed_https_get(
    monkeypatch: pytest.MonkeyPatch, action: str, path: str,
) -> None:
    transport, connection, authorize = _transport(monkeypatch)
    try:
        assert transport.request(action) == {}
    finally:
        transport.close()
    request = connection.request.call_args
    assert request.args[:2] == ('GET', path)
    assert not request.kwargs.get('body')
    assert authorize.call_count >= 2
    assert rabbitmq_transport.http.client.HTTPSConnection.call_args.args[:2] == ('192.0.2.10', 15671)


@pytest.mark.parametrize('action', [
    'purge', 'delete', 'POST', '/api/queues', 'https://attacker.invalid/',
])
def test_rabbitmq_transport_rejects_arbitrary_paths_and_mutations(
    monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    transport, connection, _ = _transport(monkeypatch)
    try:
        with pytest.raises(RabbitMQDiagnosticError, match='invalid_configuration'):
            transport.request(action)
        connection.request.assert_not_called()
    finally:
        transport.close()


@pytest.mark.parametrize(('status', 'code'), [
    (401, 'authentication_failed'), (403, 'authentication_failed'),
    (301, 'invalid_response'), (302, 'invalid_response'), (307, 'invalid_response'),
    (500, 'invalid_response'),
])
def test_rabbitmq_transport_rejects_redirect_and_auth_failure(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str,
) -> None:
    transport, connection, _ = _transport(monkeypatch, status=status)
    try:
        with pytest.raises(RabbitMQDiagnosticError) as failure:
            transport.request('status')
        assert failure.value.code == code
        assert connection.request.call_count == 1
    finally:
        transport.close()


@pytest.mark.parametrize(('body', 'code'), [
    (b'{"password":"do-not-echo"', 'invalid_response'),
    (b'\xff', 'invalid_response'),
    (b'x' * (rabbitmq_transport.MAX_WIRE_BYTES + 1), 'evidence_limit'),
])
def test_rabbitmq_transport_limits_payload_and_sanitizes_parse_failure(
    monkeypatch: pytest.MonkeyPatch, body: bytes, code: str,
) -> None:
    transport, _, _ = _transport(monkeypatch, body=body)
    try:
        with pytest.raises(RabbitMQDiagnosticError) as failure:
            transport.request('status')
        assert failure.value.code == code
        assert str(failure.value) == code
        assert 'password' not in str(failure.value)
    finally:
        transport.close()


def test_rabbitmq_transport_expired_deadline_prevents_request(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, connection, _ = _transport(monkeypatch)
    transport.deadline = time.monotonic() - 1
    try:
        with pytest.raises(RabbitMQDiagnosticError, match='timeout'):
            transport.request('status')
        connection.request.assert_not_called()
    finally:
        transport.close()


def test_rabbitmq_transport_preserves_authorization_revocation(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, connection, authorize = _transport(monkeypatch)
    authorize.side_effect = RuntimeError('revoked')
    try:
        with pytest.raises(RuntimeError, match='revoked'):
            transport.request('status')
        connection.request.assert_not_called()
    finally:
        transport.close()


def test_rabbitmq_tls_requires_verified_tls_12(monkeypatch: pytest.MonkeyPatch) -> None:
    context = MagicMock()
    monkeypatch.setattr(rabbitmq_transport.ssl, 'SSLContext', MagicMock(return_value=context))
    assert rabbitmq_transport._tls_context('valid-ca') is context
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    context.load_verify_locations.assert_called_once_with(cadata='valid-ca')


def test_rabbitmq_unknown_failure_code_never_reflects_remote_text() -> None:
    failure = RabbitMQDiagnosticError(json.dumps({'password': 'hidden'}))
    assert failure.code == 'transport_failed'
    assert str(failure) == 'transport_failed'


def test_rabbitmq_wire_budget_is_cumulative_across_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rabbitmq_transport, 'MAX_WIRE_BYTES', 3)
    transport, connection, _ = _transport(monkeypatch)
    connection.getresponse.return_value.read.side_effect = [b'{}', b'', b'{}']
    try:
        assert transport.request('status') == {}
        with pytest.raises(RabbitMQDiagnosticError, match='evidence_limit'):
            transport.request('nodes')
        assert connection.request.call_count == 2
    finally:
        transport.close()
