import ssl
import time
from unittest.mock import Mock, patch

import pymysql
import pytest
from pymysql.constants import CLIENT

from src.services import mysql_transport as transport
from test_kubernetes_grants import certificate


def test_no_tls_capability_is_rejected_before_password_authentication() -> None:
    connection = object.__new__(transport._TLSConnection)
    connection.server_capabilities = 0
    connection.ssl = True
    connection.check = Mock()
    with patch.object(pymysql.connections.Connection, '_request_authentication') as authenticate:
        with pytest.raises(transport.MySQLDiagnosticError, match='tls_failed'):
            connection._request_authentication()
    authenticate.assert_not_called()


def test_tls_authentication_uses_original_driver_only_when_ssl_advertised() -> None:
    connection = object.__new__(transport._TLSConnection)
    connection.server_capabilities = CLIENT.SSL
    connection.ssl = True
    connection.check = Mock()
    with patch.object(pymysql.connections.Connection, '_request_authentication') as authenticate:
        connection._request_authentication()
    authenticate.assert_called_once()
    connection.check.assert_called_once()


def test_tls_context_trusts_supplied_ca_and_validates_identity() -> None:
    context = transport._tls_context(certificate())
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert len(context.get_ca_certs()) == 1
    with pytest.raises(transport.MySQLDiagnosticError, match='tls_failed'):
        transport._tls_context('not-a-certificate')


def test_requests_recheck_authorization_before_each_fixed_query() -> None:
    client = object.__new__(transport.MySQLTransport)
    client.authorize = Mock(side_effect=[None, None, PermissionError('revoked')])
    client.deadline = time.monotonic() + 10
    client.connection = Mock()
    cursor = Mock()
    client.connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [{'Variable_name': 'Uptime', 'Value': '1'}]
    client.request('status')
    with pytest.raises(PermissionError):
        client.request('variables')
    assert cursor.execute.call_count == 1
    assert cursor.execute.call_args.args == (transport.QUERIES['status'],)


@pytest.mark.parametrize('action', ['SELECT * FROM users', 'drop', 'mysql.status;DELETE', 'grants'])
def test_transport_rejects_arbitrary_sql_before_network(action: str) -> None:
    client = object.__new__(transport.MySQLTransport)
    client.connection = Mock()
    with pytest.raises(transport.MySQLDiagnosticError, match='invalid_configuration'):
        client.request(action)
    client.connection.cursor.assert_not_called()


def test_deadline_exhaustion_prevents_next_query() -> None:
    client = object.__new__(transport.MySQLTransport)
    client.deadline = time.monotonic() - 1
    client.authorize = Mock()
    client.connection = Mock()
    with pytest.raises(transport.MySQLDiagnosticError, match='timeout'):
        client.request('status')
    client.connection.cursor.assert_not_called()


def test_wire_byte_budget_stops_oversized_packets_before_read() -> None:
    connection = object.__new__(transport._TLSConnection)
    connection.check = Mock()
    connection.received = transport.MAX_WIRE_BYTES
    connection._sock = Mock()
    with patch.object(pymysql.connections.Connection, '_read_bytes') as read:
        with pytest.raises(transport.MySQLDiagnosticError, match='evidence_limit'):
            connection._read_bytes(1)
    read.assert_not_called()


def test_replica_fallback_only_for_unsupported_syntax() -> None:
    from src.services.mysql_diagnostics import _Observation, _replication
    client = Mock()
    client.request.side_effect = [
        transport.MySQLDiagnosticError('query_failed'),
    ]
    with pytest.raises(transport.MySQLDiagnosticError, match='query_failed'):
        _replication(client, _Observation(1))
    assert client.request.call_count == 1
