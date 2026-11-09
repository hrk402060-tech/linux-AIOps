from unittest.mock import patch

from fastapi.testclient import TestClient


def _asset(client: TestClient) -> str:
    response = client.post('/api/v1/assets', json={'name': 'exposure-node', 'host': '192.0.2.130', 'ssh_port': 22, 'os_family': 'openeuler', 'environment': 'test', 'group_name': 'exposure', 'credential_id': None, 'labels': []})
    return response.json()['id']


def test_exposure_task_lifecycle_is_read_only(client: TestClient) -> None:
    asset_id = _asset(client)
    created = client.post('/api/v1/exposures', json={'name': '监听面检查', 'asset_ids': [asset_id]})
    assert created.status_code == 201
    assert created.json()['read_only'] is True
    with patch('src.services.exposures.execute_task'):
        started = client.post(f"/api/v1/exposures/{created.json()['id']}/start")
    assert started.json()['status'] == 'scanning'
    assert client.post(f"/api/v1/exposures/{created.json()['id']}/cancel").json()['status'] == 'cancelled'
    assert client.post(f"/api/v1/exposures/{created.json()['id']}/rerun").status_code == 201


def test_disabled_asset_is_rejected(client: TestClient) -> None:
    asset_id = _asset(client)
    client.patch(f'/api/v1/assets/{asset_id}/lifecycle', json={'status': 'disabled', 'reason': 'maintenance', 'operator': 'admin'})
    response = client.post('/api/v1/exposures', json={'name': 'blocked exposure', 'asset_ids': [asset_id]})
    assert response.status_code == 409

