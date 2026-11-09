import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services import rabbitmq_diagnostics
from src.services.rabbitmq_transport import RabbitMQDiagnosticError


def test_rabbitmq_status_projects_only_allowlisted_fields() -> None:
    transport = Mock()
    transport.request.return_value = {
        'rabbitmq_version': '4.0.2', 'cluster_name': 'test-cluster',
        'password': 'sensitive-password', 'environment': {'cookie': 'sensitive-cookie'},
        'listeners': [{'ip_address': 'sensitive-bind-address'}],
    }
    observation = rabbitmq_diagnostics._Observation(2)
    rabbitmq_diagnostics._status(transport, observation)
    status = json.loads(observation.evidence['rabbitmq.status'])
    assert status == {'rabbitmq_version': '4.0.2', 'cluster_name': 'test-cluster'}
    assert 'sensitive' not in json.dumps(observation.evidence)
    transport.request.assert_called_once_with('status')


def test_rabbitmq_nodes_are_sorted_bounded_and_projected() -> None:
    transport = Mock()
    transport.request.return_value = [
        {
            'name': f'node-{index:03}', 'running': True, 'mem_used': 1, 'mem_limit': 2,
            'fd_used': 3, 'fd_total': 4, 'sockets_used': 5, 'sockets_total': 6,
            'environment': {'cookie': 'sensitive-cookie'}, 'log_files': ['sensitive-file'],
        }
        for index in reversed(range(25))
    ]
    observation = rabbitmq_diagnostics._Observation(1)
    rabbitmq_diagnostics._nodes(transport, observation)
    result = json.loads(observation.evidence['rabbitmq.nodes'])
    assert len(result['items']) == 20
    assert result['truncated'] is True
    assert [item['name'] for item in result['items']] == [f'node-{index:03}' for index in range(20)]
    assert 'sensitive' not in json.dumps(result)


def test_rabbitmq_queues_are_sorted_bounded_and_exclude_messages_arguments() -> None:
    transport = Mock()
    transport.request.return_value = [
        {
            'name': f'queue-{index:03}', 'messages': index, 'messages_ready': index,
            'messages_unacknowledged': 0, 'consumers': 1, 'state': 'running',
            'arguments': {'password': 'sensitive-password'}, 'message_body': 'sensitive-body',
        }
        for index in reversed(range(60))
    ]
    observation = rabbitmq_diagnostics._Observation(1)
    rabbitmq_diagnostics._queues(transport, observation)
    result = json.loads(observation.evidence['rabbitmq.queues'])
    assert len(result['items']) == 50
    assert result['truncated'] is True
    assert [item['name'] for item in result['items']] == [f'queue-{index:03}' for index in range(50)]
    assert 'sensitive' not in json.dumps(result)
    assert all(set(item) == {
        'name', 'messages', 'messages_ready', 'messages_unacknowledged', 'consumers', 'state',
    } for item in result['items'])


def test_rabbitmq_consumers_return_count_without_remote_identity_or_arguments() -> None:
    transport = Mock()
    transport.request.return_value = [
        {
            'consumer_tag': 'sensitive-tag', 'arguments': {'token': 'sensitive-token'},
            'channel_details': {'user': 'sensitive-user', 'peer_host': 'sensitive-host'},
        }
        for _ in range(3)
    ]
    observation = rabbitmq_diagnostics._Observation(1)
    rabbitmq_diagnostics._consumers(transport, observation)
    assert json.loads(observation.evidence['rabbitmq.consumers']) == {'count': 3}
    assert 'sensitive' not in json.dumps(observation.evidence)


@pytest.mark.parametrize(('action', 'raw'), [
    ('status', []), ('nodes', {}), ('queues', {}), ('consumers', {}),
    ('nodes', [None]), ('queues', ['invalid']),
])
def test_rabbitmq_projection_rejects_wrong_response_shapes(action: str, raw: object) -> None:
    transport = Mock()
    transport.request.return_value = raw
    operation = getattr(rabbitmq_diagnostics, '_' + action)
    with pytest.raises(RabbitMQDiagnosticError, match='invalid_response'):
        operation(transport, rabbitmq_diagnostics._Observation(1))


def test_rabbitmq_evidence_budget_and_readonly_metadata_are_explicit() -> None:
    observation = rabbitmq_diagnostics._Observation(4)
    with pytest.raises(RabbitMQDiagnosticError, match='evidence_limit'):
        observation.store('rabbitmq.status', {'x': 'z' * 14000})
    evidence = observation.combined()
    assert evidence['read_only'] == 'true'
    assert evidence['remediation'] == 'disabled'
    assert evidence['grant_revision'] == '4'
    assert evidence['policy_version'] == 'readonly-rabbitmq-2026.10.22'


def test_rabbitmq_collection_preserves_evidence_when_authorization_revoked() -> None:
    from src.services import alert_diagnostics, diagnostic_grants
    grant = Mock(revision=3, action_ids=['rabbitmq.status', 'rabbitmq.queues'])
    transport = MagicMock()
    transport.request.return_value = {'rabbitmq_version': '4.0.2', 'cluster_name': 'test'}
    with (
        patch.object(diagnostic_grants, 'require_grant', return_value=grant),
        patch.object(alert_diagnostics, '_authorization_guard', return_value=Mock(side_effect=[
            None, None, alert_diagnostics.DiagnosticAuthorizationRequired('revoked', 3),
        ])),
        patch.object(rabbitmq_diagnostics.rabbitmq_grants, 'load_secret', return_value=('password', 'ca')),
        patch.object(rabbitmq_diagnostics, 'RabbitMQTransport', return_value=transport),
    ):
        with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired) as failure:
            rabbitmq_diagnostics.collect(Mock(), Mock(), Mock())
    assert 'rabbitmq.status' in failure.value.evidence
    assert 'rabbitmq.queues' not in failure.value.evidence
    transport.close.assert_called_once()


def test_rabbitmq_partial_collection_surfaces_action_error_without_remote_text() -> None:
    from src.services import alert_diagnostics, diagnostic_grants
    grant = Mock(revision=3, action_ids=['rabbitmq.status', 'rabbitmq.queues'])
    transport = MagicMock()
    transport.request.side_effect = [
        {'rabbitmq_version': '4.0.2', 'cluster_name': 'test'},
        RabbitMQDiagnosticError('authentication_failed'),
    ]
    with (
        patch.object(diagnostic_grants, 'require_grant', return_value=grant),
        patch.object(alert_diagnostics, '_authorization_guard', return_value=Mock()),
        patch.object(rabbitmq_diagnostics.rabbitmq_grants, 'load_secret', return_value=('password', 'ca')),
        patch.object(rabbitmq_diagnostics, 'RabbitMQTransport', return_value=transport),
    ):
        result = rabbitmq_diagnostics.collect(Mock(), Mock(), Mock())
    assert result['status'] == 'partial'
    assert json.loads(result['evidence']['action_errors']) == {'rabbitmq.queues': 'authentication_failed'}
    transport.close.assert_called_once()


def test_rabbitmq_collection_without_session_never_constructs_transport() -> None:
    from src.services import alert_diagnostics
    with patch.object(rabbitmq_diagnostics, 'RabbitMQTransport') as transport:
        with pytest.raises(alert_diagnostics.DiagnosticAuthorizationRequired):
            rabbitmq_diagnostics.collect(Mock(), Mock(), None)
    transport.assert_not_called()


def test_rabbitmq_decryption_failure_exposes_only_fixed_code() -> None:
    from src.services import alert_diagnostics, diagnostic_grants
    grant = Mock(revision=2, action_ids=['rabbitmq.status'])
    with (
        patch.object(diagnostic_grants, 'require_grant', return_value=grant),
        patch.object(alert_diagnostics, '_authorization_guard', return_value=Mock()),
        patch.object(rabbitmq_diagnostics.rabbitmq_grants, 'load_secret',
                     side_effect=ValueError('sensitive-invalid-plaintext')),
        patch.object(rabbitmq_diagnostics, 'RabbitMQTransport') as transport,
    ):
        result = rabbitmq_diagnostics.collect(Mock(), Mock(), Mock())
    assert result['status'] == 'failed'
    assert result['error_code'] == 'credential_decrypt_failed'
    assert 'sensitive-invalid-plaintext' not in json.dumps(result)
    transport.assert_not_called()
