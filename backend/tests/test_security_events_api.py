from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.alert import Alert

from src.models.monitoring import MonitoringEvent


def test_events_require_authentication(client: TestClient) -> None:
    assert client.get('/api/v1/events', headers={'Authorization': 'Bearer wrong'}).status_code == 401


@pytest.mark.parametrize('alert_status', ['open', 'resolved', 'closed'])
def test_manual_alert_state_does_not_prove_recovery(
    client: TestClient, database_session: Session, alert_status: str,
) -> None:
    asset = client.post('/api/v1/assets', json={
        'name': 'manual-alert-node', 'host': '192.0.2.91', 'ssh_port': 22,
        'os_family': 'ubuntu', 'environment': 'test', 'group_name': 'events',
        'credential_id': None, 'labels': [],
    }).json()
    connector = client.post('/api/v1/alert-connectors', json={
        'name': 'manual-recovery-test', 'source_type': 'prometheus',
    }).json()
    database_session.add(Alert(
        id='manual-alert', connector_id=connector['id'], external_id='external-1',
        title='Manually handled alert', description='No recovery evidence',
        severity='high', status=alert_status, asset_id=asset['id'],
        asset_name=asset['name'], sanitized_payload={},
    ))
    database_session.commit()
    listing = client.get('/api/v1/events?source=alert').json()
    assert listing['total'] == 1
    assert listing['items'][0]['recovered'] is False


def test_empty_events_and_unknown_disposition(client: TestClient) -> None:
    assert client.get('/api/v1/events').json() == {'items': [], 'total': 0}
    response = client.post('/api/v1/events/missing/disposition', json={
        'status': 'closed', 'reason': 'reviewed evidence', 'operator': 'admin',
    })
    assert response.status_code == 404


def test_monitoring_event_can_be_filtered_disposed_and_audited(
    client: TestClient, database_session
) -> None:
    asset = client.post('/api/v1/assets', json={
        'name': 'event-node', 'host': '192.0.2.90', 'ssh_port': 22,
        'os_family': 'openeuler', 'environment': 'production',
        'group_name': 'events', 'credential_id': None, 'labels': [],
    }).json()
    event = MonitoringEvent(
        id='event-1', asset_id=asset['id'], asset_name=asset['name'],
        metric='cpu_percent', event_type='triggered', value=95, threshold=85,
        message='CPU sustained threshold exceeded',
    )
    database_session.add(event)
    database_session.commit()

    listing = client.get(
        f"/api/v1/events?asset_id={asset['id']}&source=monitoring&severity=medium"
    ).json()
    assert listing['total'] == 1
    event_id = listing['items'][0]['id']
    changed = client.post(f'/api/v1/events/{event_id}/disposition', json={
        'status': 'in_progress', 'reason': 'Assigned to platform operations',
        'operator': 'admin',
    })
    assert changed.status_code == 200
    assert client.get('/api/v1/events?status=in_progress').json()['total'] == 1
    audit = client.get(f'/api/v1/events/{event_id}/audit').json()
    assert audit[0]['reason'] == 'Assigned to platform operations'
