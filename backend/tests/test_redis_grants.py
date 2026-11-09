from types import SimpleNamespace

import pytest

from src.services import redis_grants
from src.services.readonly_diagnostics import ReadOnlyPolicyError


def test_redis_scope_accepts_fixed_read_only_actions() -> None:
    grant = SimpleNamespace(
        kind='redis', redis_config={
            'host': '192.0.2.10', 'port': 6379, 'username': 'readonly',
            'database': 0, 'ca_fingerprint': 'SHA256:' + 'a' * 64,
        },
        host='192.0.2.10', port=6379, username='readonly',
        action_ids=['redis.status', 'redis.slowlog'],
    )
    redis_grants.require_scope(grant)


@pytest.mark.parametrize('username', ['root', 'admin', 'default'])
def test_redis_scope_rejects_privileged_identity(username: str) -> None:
    grant = SimpleNamespace(
        kind='redis', redis_config={
            'host': '192.0.2.10', 'port': 6379, 'username': username,
            'database': 0, 'ca_fingerprint': 'SHA256:' + 'a' * 64,
        },
        host='192.0.2.10', port=6379, username=username,
        action_ids=['redis.status'],
    )
    with pytest.raises(ReadOnlyPolicyError):
        redis_grants.require_scope(grant)
