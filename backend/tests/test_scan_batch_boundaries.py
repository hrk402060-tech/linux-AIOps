from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.services.auth import create_user


def task(client: TestClient) -> dict[str, object]:
    asset = client.post('/api/v1/assets', json={
        'name': 'DEMO scan batch', 'host': '192.0.2.10', 'ssh_port': 22,
        'os_family': 'openeuler', 'environment': 'test', 'labels': [],
    })
    assert asset.status_code == 201
    response = client.post('/api/v1/scan-tasks', json={
        'name': 'DEMO immutable history', 'scan_type': 'comprehensive',
        'asset_ids': [asset.json()['id']],
    })
    assert response.status_code == 201
    return response.json()


def login(client: TestClient, session: Session, role: str) -> None:
    create_user(session, 'scan-' + role, 'Test-only-Password1!', role, 'DEMO', False)
    session.commit()
    client.headers.pop('Authorization', None)
    response = client.post('/api/v1/auth/login', json={
        'username': 'scan-' + role, 'password': 'Test-only-Password1!',
    })
    assert response.status_code == 200


@pytest.mark.parametrize('role', ['admin', 'operator'])
def test_existing_batch_endpoints_preserve_evidence_and_real_actor(
    client: TestClient, database_session: Session, role: str,
) -> None:
    original = task(client)
    identifier = original['id']
    client.post(f'/api/v1/scan-tasks/{identifier}/cancel')
    before = client.get(f'/api/v1/scan-tasks/{identifier}').json()
    login(client, database_session, role)
    archived = client.patch(f'/api/v1/scan-tasks/{identifier}/archive', json={'archived': True, 'operator': 'spoofed'})
    assert archived.status_code == 200
    assert archived.json()['archived_by'] == 'scan-' + role
    assert client.get('/api/v1/scan-tasks').json() == []
    retained = client.get(f'/api/v1/scan-tasks/{identifier}').json()
    for key in ('execution_log', 'findings', 'knowledge_version', 'asset_snapshot', 'status'):
        assert retained[key] == before[key]
    assert client.get('/api/v1/scan-tasks?archive_view=archived').json()[0]['id'] == identifier
    # Deletion is now an explicit administrator operation, not an empty DELETE.
    assert client.delete(f'/api/v1/scan-tasks/{identifier}').status_code in {403, 422}
    rerun = client.post(f'/api/v1/scan-tasks/{identifier}/rerun')
    assert rerun.status_code == 201 and rerun.json()['rerun_of_id'] == identifier
    assert rerun.json()['status'] == 'queued' and rerun.json()['id'] != identifier
    restored = client.patch(f'/api/v1/scan-tasks/{identifier}/archive', json={'archived': False})
    assert restored.status_code == 200 and not restored.json()['archived']


def test_auditor_cannot_mutate_any_scan_batch_action(client: TestClient, database_session: Session) -> None:
    original = task(client)
    login(client, database_session, 'auditor')
    url = f"/api/v1/scan-tasks/{original['id']}"
    assert client.get(url).status_code == 200
    for action in ('start', 'cancel', 'rerun'):
        assert client.post(url + '/' + action).status_code == 403
    assert client.patch(url + '/archive', json={'archived': True}).status_code == 403
    assert client.get(url).json()['status'] == 'queued'


def test_archive_conflict_does_not_change_active_scan(client: TestClient) -> None:
    original = task(client)
    url = f"/api/v1/scan-tasks/{original['id']}"
    assert client.patch(url + '/archive', json={'archived': True}).status_code == 409
    retained = client.get(url).json()
    assert retained['status'] == 'queued' and not retained['archived']
    assert retained['execution_log'] == original['execution_log']
