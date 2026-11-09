from unittest.mock import Mock

import pytest

from src.services import docker_projection, docker_transport


def container(identifier: str) -> dict[str, object]:
    return {'Id': identifier, 'Names': ['/web'], 'Image': 'nginx:latest',
            'State': 'running', 'Status': 'Up 2 minutes', 'Created': 1700000000,
            'Ports': [{'PrivatePort': 80, 'PublicPort': 8080, 'Type': 'tcp'}],
            'Labels': {'secret': 'must-not-leak'}, 'Mounts': [{'Source': '/x'}]}


def test_projection_omits_sensitive_fields_and_preserves_count_for_collector_limits() -> None:
    rows = docker_projection.project_containers([container(f'{i:064x}') for i in range(50)])
    assert len(rows) == 50
    assert rows[0] == {
        'id': '0' * 64, 'names': ['/web'], 'state': 'running',
        'created_at_epoch': 1700000000,
    }
    assert 'Labels' not in rows[0] and 'Mounts' not in rows[0] and 'ports' not in rows[0]


def test_projection_rejects_non_hex_or_malformed_stats() -> None:
    with pytest.raises(docker_transport.DockerTransportError, match='invalid_response'):
        docker_projection.project_containers([container('bad')])
    with pytest.raises(docker_transport.DockerTransportError, match='invalid_response'):
        docker_projection.project_stats({'memory_stats': {}, 'cpu_stats': [], 'networks': {}})


def test_transport_only_allows_pinned_read_paths() -> None:
    transport = object.__new__(docker_transport.DockerTransport)
    transport.deadline = 100
    transport.authorize = Mock()
    transport.host = '192.0.2.20'
    transport.port = 443
    with pytest.raises(docker_transport.DockerTransportError, match='invalid_action'):
        transport.request('/v1.45/containers/abc/exec')
    with pytest.raises(docker_transport.DockerTransportError, match='invalid_action'):
        transport.request('/v1.45/containers/' + ('a' * 64) + '/stats?stream=false')
    with pytest.raises(docker_transport.DockerTransportError, match='resource_not_discovered'):
        transport.stats('not-a-container-id')


def test_stats_projection_is_bounded_and_read_only() -> None:
    body = {
        'memory_stats': {'usage': 10, 'limit': 100},
        'cpu_stats': {'cpu_usage': {'total_usage': 5}, 'system_cpu_usage': 20, 'online_cpus': 2},
        'networks': {'eth0': {'rx_bytes': 1, 'tx_bytes': 2}},
        'config': {'Env': ['SECRET=hidden']},
    }
    result = docker_projection.project_stats(body)
    assert result == {'memory_usage': 10, 'memory_limit': 100, 'cpu_usage': 5,
                      'system_cpu_usage': 20, 'online_cpus': 2,
                      'network_bytes': {'rx_bytes': 1, 'tx_bytes': 2},
                      'sample_time': None, 'cpu_percent': None, 'pids': None,
                      'metrics_available': True}
    assert 'config' not in result


@pytest.mark.parametrize('value', [-1, True, 1.5, float('nan'), float('inf'), 2**63])
def test_stats_rejects_invalid_counters(value: object) -> None:
    with pytest.raises(docker_transport.DockerTransportError, match='invalid_response'):
        docker_projection.project_stats({'memory_stats': {'usage': value}})


def test_network_limits_and_missing_counters_are_explicit() -> None:
    with pytest.raises(docker_transport.DockerTransportError, match='item_limit'):
        docker_projection.project_stats({'networks': {str(i): {} for i in range(33)}})
    result = docker_projection.project_stats({'networks': {'eth0': {'rx_bytes': 4}, 'eth1': {}}})
    assert result['network_bytes'] == {'rx_bytes': None, 'tx_bytes': None}
    assert result['metrics_available'] is False
