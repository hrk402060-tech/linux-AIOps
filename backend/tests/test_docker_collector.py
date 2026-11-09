import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import alert_diagnostics, docker_diagnostics as collector
from src.services.docker_transport import DockerTransportError


def row(identifier: int, state: str = 'running') -> dict[str, object]:
    return {'Id': f'{identifier:064x}', 'Names': ['/example'], 'State': state,
            'Created': 1700000000, 'Command': 'private-canary',
            'Labels': {'private-canary': 'hidden'}, 'Mounts': ['private-canary']}


def stats(identifier: str) -> dict[str, object]:
    return {'id': identifier, 'read': '2026-10-16T12:00:00Z',
            'memory_stats': {'usage': 10, 'limit': 100},
            'cpu_stats': {'cpu_usage': {'total_usage': 5}, 'system_cpu_usage': 20},
            'networks': {'eth0': {'rx_bytes': 1, 'tx_bytes': 2}}}


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    asset = SimpleNamespace(host='192.0.2.20')
    grant = SimpleNamespace(kind='docker', revision=4,
                            docker_config={'endpoint': 'https://192.0.2.20:8443'},
                            action_ids=['docker.containers', 'docker.resources'])
    transport = Mock()
    transport.containers.return_value = [row(1), row(2)]
    transport.stats.side_effect = stats
    guard = Mock()
    monkeypatch.setattr(collector.diagnostic_grants, 'require_grant', Mock(return_value=grant))
    monkeypatch.setattr(collector.alert_diagnostics, '_authorization_guard', Mock(return_value=guard))
    monkeypatch.setattr(collector, '_credentials', Mock(return_value=('token', 'ca')))
    monkeypatch.setattr(collector, 'DockerTransport', Mock(return_value=transport))
    return SimpleNamespace(asset=asset, grant=grant, transport=transport, guard=guard)


def collect(context: SimpleNamespace) -> dict[str, object]:
    return collector.collect(context.asset, Mock(), Mock(), expected_revision=4)


def test_scoped_collection_only_uses_discovered_ids_and_safe_projection(context: SimpleNamespace) -> None:
    result = collect(context)
    assert result['status'] == 'completed'
    evidence = result['evidence']
    assert evidence['grant_revision'] == '4' and evidence['remediation'] == 'disabled'
    assert 'private-canary' not in json.dumps(result)
    assert [call.args[0] for call in context.transport.stats.call_args_list] == [
        f'{1:064x}', f'{2:064x}']
    resources = json.loads(evidence['docker.resources'])
    assert resources['items'][0]['stats']['cpu_percent'] is None


@pytest.mark.parametrize('count', [4, 11, 50])
def test_limits_are_explicitly_partial_not_silent_success(context: SimpleNamespace, count: int) -> None:
    context.transport.containers.return_value = [row(i) for i in range(count)]
    result = collect(context)
    assert result['status'] == 'partial'
    assert context.transport.stats.call_count <= 3
    containers = json.loads(result['evidence']['docker.containers'])
    assert len(containers['items']) <= 10 and containers['observed_count'] == count
    assert bool(json.loads(result['evidence']['action_errors']))


def test_large_container_names_keep_safe_prefix_when_evidence_budget_is_hit(
    context: SimpleNamespace,
) -> None:
    long_name = '/' + ('x' * 128)
    context.transport.containers.return_value = [
        {**row(i), 'Names': [long_name] * 10} for i in range(10)
    ]
    result = collect(context)
    assert result['status'] == 'partial'
    containers = json.loads(result['evidence']['docker.containers'])
    assert containers['items']
    assert containers['projected_count'] == len(containers['items'])
    assert containers['observed_count'] == 10
    assert containers['truncated'] is True
    assert json.loads(result['evidence']['action_errors'])['docker.containers'] == 'evidence_limit'


def test_empty_list_is_valid_but_not_a_health_verdict(context: SimpleNamespace) -> None:
    context.transport.containers.return_value = []
    result = collect(context)
    assert result['status'] == 'completed'
    context.transport.stats.assert_not_called()
    assert '健康' not in result['summary']


def test_stopped_container_never_requests_stats_or_fabricates_zero(context: SimpleNamespace) -> None:
    context.transport.containers.return_value = [row(1, 'exited')]
    result = collect(context)
    context.transport.stats.assert_not_called()
    item = json.loads(result['evidence']['docker.resources'])['items'][0]
    assert item['status'] == 'not_sampled' and 'stats' not in item


@pytest.mark.parametrize('error', [
    alert_diagnostics.DiagnosticAuthorizationRequired,
    alert_diagnostics.DiagnosticReadInterrupted,
])
def test_interrupt_retains_list_and_first_stats(context: SimpleNamespace, error: type[Exception]) -> None:
    def interrupt_second(identifier: str) -> dict[str, object]:
        if identifier == f'{2:064x}':
            raise error('interrupted')
        return stats(identifier)
    context.transport.stats.side_effect = interrupt_second
    with pytest.raises(error) as caught:
        collect(context)
    assert caught.value.revision == 4
    assert 'docker.containers' in caught.value.evidence
    resources = json.loads(caught.value.evidence['docker.resources'])
    assert [item['id'] for item in resources['items']] == [f'{1:064x}']
    assert caught.value.evidence['remediation'] == 'disabled'


def test_resource_disappeared_keeps_other_samples_as_partial(context: SimpleNamespace) -> None:
    context.transport.stats.side_effect = [DockerTransportError('http_not_found'), stats(f'{2:064x}')]
    result = collect(context)
    assert result['status'] == 'partial'
    items = json.loads(result['evidence']['docker.resources'])['items']
    assert items[0] == {'id': f'{1:064x}', 'status': 'unavailable', 'error_code': 'http_not_found'}
    assert items[1]['status'] == 'sampled'


def test_returned_stats_id_must_match_discovered_target(context: SimpleNamespace) -> None:
    context.transport.stats.side_effect = lambda _: stats(f'{9:064x}')
    result = collect(context)
    assert result['status'] == 'partial'
    assert 'invalid_response' in result['evidence']['action_errors']
    assert f'{9:064x}' not in json.dumps(result)


def test_credential_failure_never_opens_transport(
    context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector, '_credentials', Mock(side_effect=DockerTransportError('credential_decrypt_failed')))
    result = collect(context)
    assert result['status'] == 'failed' and result['error_code'] == 'credential_decrypt_failed'
    context.transport.containers.assert_not_called()


def test_scope_rejects_malformed_or_duplicate_actions_before_transport(context: SimpleNamespace) -> None:
    context.grant.action_ids = [['invalid']]
    result = collect(context)
    assert result['status'] == 'failed' and result['error_code'] == 'invalid_configuration'
    context.transport.containers.assert_not_called()


def test_partial_store_does_not_double_count_overwritten_evidence() -> None:
    observation = collector._Observation(1)
    observation.store('docker.resources', {'value': 'x' * 5000})
    observation.store('docker.resources', {'value': 'y' * 5001})
    assert len(observation.evidence['docker.resources']) < collector.MAX_EVIDENCE_CHARS
