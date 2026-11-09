from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, User
from src.services import alert_notifications as notifications
from src.services.auth import hash_token


@pytest.mark.parametrize('role', ['operator', 'auditor'])
def test_notification_admin_boundaries_and_human_ack(
    client: TestClient, database_session: Session, role: str,
) -> None:
    item = notifications.enqueue(database_session, 'permissions-test', 'asset_mapping_failed', None, None, 'Alert', 'Safe body')
    item.status = 'sent'
    item.sent_at = notifications.utc_now()
    token = uuid4().hex
    user = User(id=str(uuid4()), username=f'notify-{role}', display_name=role,
                password_hash='unused', role=role, enabled=True, must_change_password=False)
    database_session.add(user)
    database_session.add(AuthSession(id=str(uuid4()), user_id=user.id, token_hash=hash_token(token),
                                    expires_at=notifications.utc_now() + timedelta(hours=1)))
    database_session.commit()
    client.headers.pop('Authorization', None)
    client.cookies.set('sentinel_session', token)
    assert client.get('/api/v1/alert-notification-config').status_code == 200
    assert client.get('/api/v1/alert-notifications').status_code == 200
    assert client.patch('/api/v1/alert-notification-config', json={'enabled': False}).status_code == 403
    assert client.post(f'/api/v1/alert-notifications/{item.id}/retry').status_code == 403
    acknowledged = client.post(f'/api/v1/alert-notifications/{item.id}/acknowledge')
    assert acknowledged.status_code == (403 if role == 'auditor' else 200)


def test_unmatched_receipts_are_replay_protected_and_deduped(database_session: Session) -> None:
    from src.models.alert import AlertConnector
    connector = AlertConnector(id=str(uuid4()), name='mapping-tests', source_type='generic', status='active',
                               encrypted_secret=b'unused', nonce=b'012345678901', key_version='v1',
                               secret_hint='mask', operator='admin')
    database_session.add(connector)
    database_session.commit()
    first = notifications.enqueue_mapping_failure(database_session, connector.id, 'external-one', 'nonce-one', 'Alert', 'Asset mapping failed')
    database_session.commit()
    again = notifications.enqueue_mapping_failure(database_session, connector.id, 'external-one', 'nonce-two', 'Alert', 'Asset mapping failed')
    database_session.commit()
    assert first.id == again.id
    assert notifications.mapping_nonce_used(database_session, connector.id, 'nonce-one')
    assert notifications.mapping_nonce_used(database_session, connector.id, 'nonce-two')
    with pytest.raises(notifications.NotificationConflictError):
        notifications.enqueue_mapping_failure(database_session, connector.id, 'external-one', 'nonce-one', 'Alert', 'Asset mapping failed')
    assert notifications.list_notifications(database_session, 0, 50)['total'] == 1


def test_summary_does_not_equate_sent_with_ack(database_session: Session) -> None:
    item = notifications.enqueue(database_session, 'summary-test', 'asset_mapping_failed', None, None, 'Alert', 'Safe body')
    item.status = 'sent'
    database_session.commit()
    result = notifications.summarize(database_session, None)
    assert result['sent'] == 1 and result['acknowledged'] == 0


def test_unmapped_common_title_alerts_retain_safe_correlation(database_session: Session) -> None:
    from src.models.alert import AlertConnector

    connector = AlertConnector(id=str(uuid4()), name='safe-source', source_type='generic', status='active',
                               encrypted_secret=b'unused', nonce=b'012345678901', key_version='v1',
                               secret_hint='mask', operator='admin')
    database_session.add(connector)
    database_session.commit()
    for index, host in enumerate(('192.0.2.10', '192.0.2.11')):
        notifications.enqueue_mapping_failure(database_session, connector.id, f'event-{index}', f'nonce-{index}',
            'Common alert', 'Unmatched', source='safe-source',
            locators={'asset_host': host, 'instance': f'{host}:9100 token=hidden', 'password': 'must-not-store'})
    database_session.commit()
    items = notifications.list_notifications(database_session, 0, 100)['items']
    assert {item['external_id'] for item in items} == {'event-0', 'event-1'}
    assert {item['asset_locator']['asset_host'] for item in items} == {'192.0.2.10', '192.0.2.11'}
    assert all(item['source'] == 'safe-source' and 'Common alert' in item['title'] for item in items)
    assert 'hidden' not in str(items) and 'must-not-store' not in str(items)
    assert all('password' not in item['asset_locator'] for item in items)
