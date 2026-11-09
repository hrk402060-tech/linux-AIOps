from unittest.mock import patch

from fastapi.testclient import TestClient


def _asset(client: TestClient) -> str:
    response = client.post('/api/v1/assets', json={'name': 'baseline-node', 'host': '192.0.2.120', 'ssh_port': 22, 'os_family': 'openeuler', 'environment': 'test', 'group_name': 'compliance', 'credential_id': None, 'labels': []})
    return response.json()['id']


def test_baseline_task_lifecycle_is_read_only(client: TestClient) -> None:
    asset_id = _asset(client)
    created = client.post('/api/v1/baselines', json={'name': 'Linux 安全基线', 'asset_ids': [asset_id]})
    assert created.status_code == 201
    assert created.json()['read_only'] is True
    assert created.json()['policy_version'] == '2026.09'
    with patch('src.services.baselines.execute_task'):
        started = client.post(f"/api/v1/baselines/{created.json()['id']}/start")
    assert started.json()['status'] == 'scanning'
    cancelled = client.post(f"/api/v1/baselines/{created.json()['id']}/cancel")
    assert cancelled.json()['status'] == 'cancelled'
    rerun = client.post(f"/api/v1/baselines/{created.json()['id']}/rerun")
    assert rerun.status_code == 201
    assert rerun.json()['rerun_of_id'] == created.json()['id']


def test_disabled_asset_is_rejected(client: TestClient) -> None:
    asset_id = _asset(client)
    client.patch(f'/api/v1/assets/{asset_id}/lifecycle', json={'status': 'disabled', 'reason': 'maintenance', 'operator': 'admin'})
    response = client.post('/api/v1/baselines', json={'name': 'blocked baseline', 'asset_ids': [asset_id]})
    assert response.status_code == 409

