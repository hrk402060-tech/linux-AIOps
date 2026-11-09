from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.models.scan_task import ScanSchedule
from src.services.scan_schedules import execute_due


def _asset(client: TestClient) -> str:
    response = client.post('/api/v1/assets', json={
        'name': 'scheduled-node', 'host': '192.0.2.101', 'ssh_port': 22,
        'os_family': 'openeuler', 'environment': 'test', 'group_name': 'scheduled',
        'credential_id': None, 'labels': [],
    })
    return response.json()['id']


def test_schedule_create_toggle_and_task_history(client: TestClient) -> None:
    asset_id = _asset(client)
    created = client.post('/api/v1/scan-schedules', json={
        'name': '每日漏洞巡检', 'scan_type': 'comprehensive', 'asset_ids': [asset_id],
        'schedule_kind': 'daily', 'daily_time': '02:30', 'timezone': 'Asia/Shanghai',
    })
    assert created.status_code == 201
    body = created.json()
    assert body['enabled'] is True
    assert body['asset_snapshot'][0]['name'] == 'scheduled-node'
    assert body['next_run_at'] is not None
    paused = client.patch(
        f"/api/v1/scan-schedules/{body['id']}/enabled", json={'enabled': False}
    )
    assert paused.json()['enabled'] is False
    assert client.get(f"/api/v1/scan-schedules/{body['id']}/tasks").json() == []


def test_due_schedule_creates_linked_read_only_task(client: TestClient) -> None:
    asset_id = _asset(client)
    created = client.post('/api/v1/scan-schedules', json={
        'name': '周期巡检', 'scan_type': 'host_vulnerability', 'asset_ids': [asset_id],
        'schedule_kind': 'interval', 'interval_hours': 4, 'timezone': 'Asia/Shanghai',
    }).json()
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        schedule = session.get(ScanSchedule, created['id'])
        schedule.next_run_at = now - timedelta(minutes=1)
        session.commit()
    with patch('src.services.scan_schedules.scan_tasks.execute_task'):
        assert execute_due(
            client.app.state.session_factory, client.app.state.credential_vault, now
        ) == 1
    tasks = client.get(f"/api/v1/scan-schedules/{created['id']}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]['read_only'] is True
    assert tasks[0]['schedule_id'] == created['id']
    assert tasks[0]['status'] == 'scanning'


def test_disabled_asset_cannot_be_scheduled(client: TestClient) -> None:
    asset_id = _asset(client)
    client.patch(f'/api/v1/assets/{asset_id}/lifecycle', json={
        'status': 'disabled', 'reason': 'maintenance', 'operator': 'admin',
    })
    response = client.post('/api/v1/scan-schedules', json={
        'name': 'blocked plan', 'scan_type': 'comprehensive', 'asset_ids': [asset_id],
        'schedule_kind': 'interval', 'interval_hours': 6, 'timezone': 'Asia/Shanghai',
    })
    assert response.status_code == 400


def test_schedule_cannot_be_reenabled_after_asset_is_disabled(client: TestClient) -> None:
    asset_id = _asset(client)
    schedule = client.post('/api/v1/scan-schedules', json={
        'name': 'paused plan', 'scan_type': 'comprehensive', 'asset_ids': [asset_id],
        'schedule_kind': 'interval', 'interval_hours': 6, 'timezone': 'Asia/Shanghai',
    }).json()
    client.patch(f"/api/v1/scan-schedules/{schedule['id']}/enabled", json={'enabled': False})
    client.patch(f'/api/v1/assets/{asset_id}/lifecycle', json={
        'status': 'disabled', 'reason': 'maintenance', 'operator': 'admin',
    })

    response = client.patch(
        f"/api/v1/scan-schedules/{schedule['id']}/enabled", json={'enabled': True}
    )

    assert response.status_code == 400
    assert client.get('/api/v1/scan-schedules/missing/tasks').status_code == 404
