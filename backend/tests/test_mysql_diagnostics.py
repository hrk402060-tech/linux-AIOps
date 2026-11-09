import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services import mysql_diagnostics


def test_mysql_status_uses_fixed_read_only_queries_and_projects_rows() -> None:
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{'Variable_name': 'Uptime', 'Value': '42'}, {'Variable_name': 'Threads_connected', 'Value': '3'}],
        [{'Variable_name': 'read_only', 'Value': 'ON'}, {'Variable_name': 'version', 'Value': '8.0'}],
    ]
    transport = MagicMock()
    transport.request.side_effect = cursor.fetchall.side_effect
    observation = mysql_diagnostics._Observation(2)

    mysql_diagnostics._status(transport, observation)

    assert transport.request.call_count == 2
    assert transport.request.call_args_list[0].args == ('status',)
    assert 'mysql.status' in observation.evidence
    assert 'Threads_connected' in observation.evidence['mysql.status']


def test_mysql_replication_does_not_return_unbounded_server_fields() -> None:
    transport = MagicMock()
    transport.request.return_value = [{
        'Replica_IO_Running': 'Yes', 'Replica_SQL_Running': 'Yes', 'Last_SQL_Errno': 1062,
        'Last_SQL_Error': 'INSERT INTO users VALUES (secret)', 'Secret_Field': 'must-not-leak',
        'Source_Host': 'secret-password', 'Seconds_Behind_Source': 0,
    }]
    observation = mysql_diagnostics._Observation(1)

    mysql_diagnostics._replication(transport, observation)

    assert 'Secret_Field' not in observation.evidence['mysql.replication']
    assert 'INSERT' not in observation.evidence['mysql.replication']
    assert 'secret' not in observation.evidence['mysql.replication']
    assert '1062' in observation.evidence['mysql.replication']


def test_unexpected_status_fields_are_not_evidence() -> None:
    transport = Mock()
    transport.request.side_effect = [
        [{'Variable_name': 'Uptime', 'Value': '42'}, {'Variable_name': 'password', 'Value': 'secret'}],
        [{'Variable_name': 'version', 'Value': "SELECT secret FROM users"}],
    ]
    with pytest.raises(mysql_diagnostics.MySQLDiagnosticError, match='invalid_response'):
        mysql_diagnostics._status(transport, mysql_diagnostics._Observation(1))


def test_collector_keeps_evidence_and_stops_after_revocation() -> None:
    from src.services import alert_diagnostics, diagnostic_grants
    grant = Mock(revision=3, action_ids=['mysql.status', 'mysql.replication'])
    transport = MagicMock()
    transport.request.side_effect = [
        [{'Variable_name': 'Uptime', 'Value': '42'}],
        [{'Variable_name': 'read_only', 'Value': 'ON'}],
    ]
    with (
        patch.object(diagnostic_grants, 'require_grant', return_value=grant),
        patch.object(alert_diagnostics, '_authorization_guard', return_value=Mock(side_effect=[
            None, None, alert_diagnostics.DiagnosticAuthorizationRequired('revoked', 3),
        ])),
        patch.object(mysql_diagnostics.mysql_grants, 'load_secret', return_value=('password', 'ca')),
        patch.object(mysql_diagnostics, 'MySQLTransport', return_value=transport),
    ):
        with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired) as failure:
            mysql_diagnostics.collect(Mock(), Mock(), Mock())
    assert 'mysql.status' in failure.value.evidence
    assert 'mysql.replication' not in failure.value.evidence
    transport.close.assert_called_once()


def test_evidence_limit_and_partial_query_error_are_explicit() -> None:
    observation = mysql_diagnostics._Observation(1)
    with pytest.raises(mysql_diagnostics.MySQLDiagnosticError, match='evidence_limit'):
        observation.store('mysql.status', {'x': 'z' * 14000})
    observation.store('mysql.status', {'uptime': 1})
    observation.errors['mysql.replication'] = 'query_failed'
    result = mysql_diagnostics._result(observation)
    assert result['status'] == 'partial'
    assert json.loads(result['evidence']['action_errors']) == {'mysql.replication': 'query_failed'}
