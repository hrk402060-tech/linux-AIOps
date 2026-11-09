from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, User
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.monitoring import MonitoringEvent
from src.services.auth import hash_token


def _asset(client: TestClient) -> dict[str, object]:
    return client.post('/api/v1/assets', json={
        'name': 'incident-node', 'host': '192.0.2.111', 'ssh_port': 22,
        'os_family': 'openeuler', 'environment': 'production',
        'group_name': 'incidents', 'credential_id': None, 'labels': [],
    }).json()


def test_incident_lifecycle_is_append_only_and_requires_valid_transition(
    client: TestClient, database_session
) -> None:
    asset = _asset(client)
    database_session.add(MonitoringEvent(
        id='incident-event-1', asset_id=asset['id'], asset_name=asset['name'],
        metric='cpu_percent', event_type='triggered', value=96, threshold=85,
        message='CPU sustained threshold exceeded',
    ))
    database_session.commit()

    created = client.post('/api/v1/incidents', json={
        'title': '生产节点 CPU 异常', 'description': '需要值班人员确认影响范围',
        'severity': 'high', 'asset_id': asset['id'], 'alert_id': 'monitoring:incident-event-1',
    })
    assert created.status_code == 201
    incident = created.json()
    assert incident['status'] == 'open'
    assert incident['asset_id'] == asset['id']
    assert incident['sla_target_minutes'] == 60
    assert incident['sla_status'] == 'on_track'
    assert incident['sla_due_at']

    invalid = client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
        'status': 'closed', 'note': '直接关闭不允许',
    })
    assert invalid.status_code == 409

    for status in ('acknowledged', 'in_progress', 'resolved', 'closed'):
        response = client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
            'status': status, 'note': f'人工确认 {status}',
        })
        assert response.status_code == 200
    history = client.get(f"/api/v1/incidents/{incident['id']}/timeline")
    assert history.status_code == 200
    assert [item['status'] for item in history.json()] == [
        'closed', 'resolved', 'in_progress', 'acknowledged', 'open'
    ]
    assert client.delete(f"/api/v1/incidents/{incident['id']}").status_code == 405


def test_incident_filters_and_unknown_asset_are_rejected(client: TestClient) -> None:
    response = client.post('/api/v1/incidents', json={
        'title': '孤立故障', 'description': '没有有效资产', 'severity': 'medium',
        'asset_id': 'missing-asset',
    })
    assert response.status_code == 404
    assert client.get('/api/v1/incidents?status=open&severity=critical').json() == {
        'total': 0, 'items': []
    }


def test_incident_rejects_inactive_assets_and_mismatched_alerts(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client)
    other = _asset_named(client, 'incident-node-other', '192.0.2.112')
    database_session.add(MonitoringEvent(
        id='other-asset-event', asset_id=other['id'], asset_name=other['name'],
        metric='memory_percent', event_type='triggered', value=92, threshold=85,
        message='Memory threshold exceeded',
    ))
    database_session.commit()
    payload = {
        'title': 'Source mismatch', 'description': 'Alert belongs to another asset',
        'severity': 'high', 'asset_id': asset['id'],
        'alert_id': 'monitoring:other-asset-event',
    }
    assert client.post('/api/v1/incidents', json=payload).status_code == 409
    payload['alert_id'] = 'monitoring:missing-event'
    assert client.post('/api/v1/incidents', json=payload).status_code == 404

    stored = database_session.get(Asset, asset['id'])
    assert stored is not None
    stored.lifecycle_status = AssetLifecycleStatus.DISABLED
    database_session.commit()
    payload['alert_id'] = None
    assert client.post('/api/v1/incidents', json=payload).status_code == 409
    stored.lifecycle_status = AssetLifecycleStatus.ARCHIVED
    database_session.commit()
    assert client.post('/api/v1/incidents', json=payload).status_code == 409


def test_incident_operator_is_server_identity_and_close_requires_note(
    client: TestClient,
) -> None:
    asset = _asset(client)
    created = client.post('/api/v1/incidents', json={
        'title': 'Manual handling', 'description': 'Verify operator and close note',
        'severity': 'medium', 'asset_id': asset['id'], 'operator': 'forged-user',
    })
    assert created.status_code == 201
    incident = created.json()
    assert incident['operator'] == 'break-glass-admin'
    for status in ('acknowledged', 'in_progress', 'resolved'):
        assert client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
            'status': status, 'note': status, 'operator': 'forged-user',
        }).status_code == 200
    assert client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
        'status': 'closed', 'note': '   ', 'operator': 'forged-user',
    }).status_code == 409
    closed = client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
        'status': 'closed', 'note': '  Human verified recovery  ',
        'operator': 'forged-user',
    })
    assert closed.status_code == 200
    timeline = client.get(f"/api/v1/incidents/{incident['id']}/timeline").json()
    assert timeline[0]['note'] == 'Human verified recovery'
    assert all(item['operator'] == 'break-glass-admin' for item in timeline)


def test_incident_auditor_is_read_only(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client)
    incident = client.post('/api/v1/incidents', json={
        'title': 'Auditor access', 'description': 'Auditor must remain read only',
        'severity': 'low', 'asset_id': asset['id'],
    }).json()
    user = User(
        id=str(uuid4()), username='incident-auditor', display_name='Incident Auditor',
        password_hash='unused', role='auditor', enabled=True, must_change_password=False,
        asset_scope='all',
    )
    token = uuid4().hex
    database_session.add(user)
    database_session.add(AuthSession(
        id=str(uuid4()), user_id=user.id, token_hash=hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    database_session.commit()
    client.headers.pop('Authorization')
    client.cookies.set('sentinel_session', token)
    assert client.get('/api/v1/incidents').status_code == 200
    assert client.get(f"/api/v1/incidents/{incident['id']}/timeline").status_code == 200
    assert client.post('/api/v1/incidents', json={
        'title': 'Denied create', 'description': 'Read only', 'severity': 'low',
        'asset_id': asset['id'],
    }).status_code == 403
    assert client.patch(f"/api/v1/incidents/{incident['id']}/status", json={
        'status': 'acknowledged', 'note': 'Denied update',
    }).status_code == 403


def _asset_named(client: TestClient, name: str, host: str) -> dict[str, object]:
    return client.post('/api/v1/assets', json={
        'name': name, 'host': host, 'ssh_port': 22, 'os_family': 'openeuler',
        'environment': 'production', 'group_name': 'incidents',
        'credential_id': None, 'labels': [],
    }).json()
