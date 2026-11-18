from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def managed_user(client: TestClient) -> Generator[tuple[TestClient, str], None, None]:
    response = client.post('/api/v1/auth/users', json={
        'username': 'feature-operator', 'password': 'Feature-Test8!', 'role': 'operator',
    })
    assert response.status_code == 201
    with TestClient(client.app) as account:
        login = account.post('/api/v1/auth/login', json={
            'username': 'feature-operator', 'password': 'Feature-Test8!',
        })
        assert login.status_code == 200
        assert login.json()['permissions'] == []
        yield account, response.json()['id']


def save_permissions(client: TestClient, user_id: str, permissions: list[str]) -> None:
    endpoint = '/api/v1/auth/access-policy'
    document = client.get(endpoint).json()
    result = client.put(endpoint, headers={'If-Match': document['revision']}, json={
        'roles': [], 'groups': [], 'users': [{
            'user_id': user_id, 'role_ids': [], 'group_ids': [], 'permissions': permissions,
        }],
    })
    assert result.status_code == 200, result.text


@pytest.mark.parametrize('path', [
    '/dashboard', '/monitoring/fleet', '/assets', '/scans', '/scan-tasks',
    '/vulnerabilities', '/operations/overview', '/ops-assistant/conversations',
    '/terminal/sessions', '/agent', '/audit-log', '/system/status',
])
def test_new_operator_cannot_call_ungranted_feature(
    managed_user: tuple[TestClient, str], path: str,
) -> None:
    account, _ = managed_user
    response = account.get(f'/api/v1{path}')
    assert response.status_code == (404 if path == '/scans' else 403)


def test_grant_and_revoke_do_not_require_new_session(
    client: TestClient, managed_user: tuple[TestClient, str],
) -> None:
    account, user_id = managed_user
    save_permissions(client, user_id, ['scans:read', 'server-monitoring:read'])
    assert account.get('/api/v1/auth/me').json()['permissions'] == ['scans:read', 'server-monitoring:read']
    assert account.get('/api/v1/scan-tasks').status_code == 200
    assert account.get('/api/v1/assets').status_code == 200
    assert account.post('/api/v1/assets', json={}).status_code == 403
    assert account.post('/api/v1/scan-tasks', json={}).status_code == 403
    assert account.get('/api/v1/dashboard').status_code == 403
    assert account.get('/api/v1/monitoring/summary').status_code == 200
    assert account.get('/api/v1/auth/access-policy').status_code == 403
    save_permissions(client, user_id, [])
    assert account.get('/api/v1/scan-tasks').status_code == 403
    assert account.get('/api/v1/auth/me').json()['permissions'] == []


def test_terminal_assistant_requires_independent_permission(
    client: TestClient, managed_user: tuple[TestClient, str],
) -> None:
    account, user_id = managed_user
    save_permissions(client, user_id, ['terminal:read', 'terminal:write'])
    assert account.get('/api/v1/terminal/sessions').status_code == 200
    assert account.post('/api/v1/terminal/assistant/chat', json={}).status_code == 403
    assert account.get('/api/v1/ops-assistant/conversations').status_code == 403
