import json
from unittest.mock import Mock

import pytest

from src.services import redis_diagnostics
from src.services.redis_transport import RedisDiagnosticError


def test_status_memory_clients_and_slowlog_projection() -> None:
    transport = Mock()
    transport.request.side_effect = [
        '# Server\r\nredis_version:7.2.1\r\nuptime_in_seconds:42\r\nrole:master\r\n',
        '# Memory\r\nused_memory:100\r\nused_memory_peak:120\r\nmaxmemory:0\r\nmem_fragmentation_ratio:1.20\r\n',
        '# Clients\r\nconnected_clients:3\r\nblocked_clients:0\r\n',
        [[12, 1_700_000_000, 900, [b'GET', b'secret-value']]],
    ]
    observation = redis_diagnostics._Observation(2)
    redis_diagnostics._status(transport, observation)
    redis_diagnostics._memory(transport, observation)
    redis_diagnostics._clients(transport, observation)
    redis_diagnostics._slowlog(transport, observation)
    assert json.loads(observation.evidence['redis.status'])['redis_version'] == '7.2.1'
    assert json.loads(observation.evidence['redis.memory'])['used_memory'] == 100
    assert json.loads(observation.evidence['redis.clients'])['connected_clients'] == 3
    slowlog = json.loads(observation.evidence['redis.slowlog'])
    assert slowlog == {'count': 1, 'latest_id': 12, 'max_duration_microseconds': 900}
    assert 'secret-value' not in json.dumps(observation.evidence)


def test_projection_rejects_non_numeric_client_counter() -> None:
    transport = Mock()
    transport.request.return_value = '# Clients\r\nconnected_clients:nope\r\n'
    with pytest.raises(RedisDiagnosticError) as failure:
        redis_diagnostics._clients(transport, redis_diagnostics._Observation(1))
    assert failure.value.code == 'invalid_response'
