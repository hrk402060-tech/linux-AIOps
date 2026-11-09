from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert_notification import AlertNotification, AlertNotificationAudit
from src.models.auth import AuthSession, User
from src.services.auth import hash_token
from src.schemas.alert_notification import NotificationConfigUpdate
from src.services import alert_notification_config as config
from src.services import alert_notification_delivery as delivery
from src.services import alert_notifications as notifications


def _configured(client: TestClient) -> dict[str, object]:
    response = client.patch('/api/v1/alert-notification-config', json={
        'enabled': True, 'host': 'smtp.example.test', 'port': 587,
        'security': 'starttls', 'username': 'mailer', 'password': 'mail-secret-test',
        'sender': 'ops@example.test', 'recipients': ['admin@example.test'],
    })
    assert response.status_code == 200
    return response.json()


def _enqueue(session: Session) -> AlertNotification:
    item = notifications.enqueue(
        session, 'dedupe-one', 'asset_mapping_failed', None, None,
        'Alert password=hidden', 'Diagnosis token=private-value',
    )
    session.commit()
    return item


def test_config_masks_encrypts_and_retains_password(client: TestClient, database_session: Session) -> None:
    assert client.get('/api/v1/alert-notification-config').json()['configured'] is False
    body = _configured(client)
    assert body['password_configured'] is True
    assert 'mail-secret-test' not in str(body)
    stored = config.get_record(database_session)
    assert stored is not None and stored.encrypted_password != b'mail-secret-test'
    first = stored.encrypted_password
    response = client.patch('/api/v1/alert-notification-config', json={
        **{key: body[key] for key in ('enabled', 'host', 'port', 'security', 'username', 'sender', 'recipients')},
        'host': 'smtp2.example.test',
    })
    assert response.status_code == 200
    database_session.refresh(stored)
    assert stored.encrypted_password == first
    audits = database_session.scalars(select(AlertNotificationAudit)).all()
    assert audits and 'mail-secret-test' not in str([item.detail for item in audits])


@pytest.mark.parametrize('field,value', [
    ('host', 'smtp.example.test\r\nInjected: bad'), ('sender', 'a@example.test\nBcc: x@y.test'),
    ('recipients', ['bad-address']), ('recipients', ['a@example.test'] * 21),
    ('port', 0), ('security', 'none'), ('host', 'https://smtp.example.test'),
])
def test_config_rejects_invalid_hosts_headers_addresses(client: TestClient, field: str, value: object) -> None:
    payload = {'enabled': True, 'host': 'smtp.example.test', 'port': 587, 'security': 'starttls',
               'username': '', 'sender': 'ops@example.test', 'recipients': ['admin@example.test']}
    payload[field] = value
    assert client.patch('/api/v1/alert-notification-config', json=payload).status_code == 422


def test_queue_is_durable_deduped_and_blocked_without_config(database_session: Session) -> None:
    item = _enqueue(database_session)
    again = notifications.enqueue(database_session, 'dedupe-one', 'asset_mapping_failed', None, None, 'other', 'other')
    database_session.commit()
    assert item.id == again.id and item.status == 'blocked'
    assert item.attempts == 0 and 'private-value' not in item.body and 'hidden' not in item.subject
    assert delivery.claim_next(database_session) is None
    database_session.refresh(item)
    assert item.status == 'blocked' and item.attempts == 0


def test_config_enable_unblocks_once_and_claim_token_prevents_stale_finalize(client: TestClient, database_session: Session) -> None:
    item = _enqueue(database_session)
    _configured(client)
    claim = delivery.claim_next(database_session)
    assert claim is not None and claim[0].id == item.id
    assert delivery.claim_next(database_session) is None
    assert delivery.finalize(database_session, item.id, 'stale-token', None) is False
    assert delivery.finalize(database_session, item.id, claim[1], None) is True
    database_session.refresh(item)
    assert item.status == 'sent' and item.attempts == 1 and item.sent_at is not None


def test_expired_lease_recovered_and_retry_budget_bounded(client: TestClient, database_session: Session) -> None:
    _configured(client)
    item = _enqueue(database_session)
    claim = delivery.claim_next(database_session)
    assert claim is not None
    item.lease_expires_at = notifications.utc_now() - timedelta(seconds=1)
    database_session.commit()
    recovered = delivery.claim_next(database_session)
    assert recovered is not None and recovered[1] != claim[1]
    assert delivery.finalize(database_session, item.id, claim[1], None) is False
    assert delivery.finalize(database_session, item.id, recovered[1], 'smtp_unavailable') is True
    for _ in range(3):
        database_session.refresh(item)
        item.next_attempt_at = notifications.utc_now() - timedelta(seconds=1)
        database_session.commit()
        current = delivery.claim_next(database_session)
        assert current is not None
        delivery.finalize(database_session, item.id, current[1], 'smtp_unavailable')
    database_session.refresh(item)
    assert item.status == 'failed' and item.attempts == item.max_attempts == 5
    assert delivery.claim_next(database_session) is None
    response = client.post(f'/api/v1/alert-notifications/{item.id}/retry')
    assert response.status_code == 409


def test_ack_requires_sent_is_human_and_idempotent(client: TestClient, database_session: Session) -> None:
    item = _enqueue(database_session)
    assert client.post(f'/api/v1/alert-notifications/{item.id}/acknowledge').status_code == 403
    token = uuid4().hex
    user = User(id=str(uuid4()), username='human-admin', display_name='Human admin', password_hash='unused',
                role='admin', enabled=True, must_change_password=False)
    database_session.add(user)
    database_session.add(AuthSession(id=str(uuid4()), user_id=user.id, token_hash=hash_token(token),
                                    expires_at=notifications.utc_now() + timedelta(hours=1)))
    database_session.commit()
    client.headers.pop('Authorization', None)
    client.cookies.set('sentinel_session', token)
    assert client.post(f'/api/v1/alert-notifications/{item.id}/acknowledge').status_code == 409
    _configured(client)
    claim = delivery.claim_next(database_session)
    assert claim is not None
    delivery.finalize(database_session, item.id, claim[1], None)
    response = client.post(f'/api/v1/alert-notifications/{item.id}/acknowledge')
    assert response.status_code == 200
    assert response.json()['acknowledged_by'] == 'human-admin'
    timestamp = response.json()['acknowledged_at']
    again = client.post(f'/api/v1/alert-notifications/{item.id}/acknowledge')
    assert again.json()['acknowledged_at'] == timestamp
    assert client.post(f'/api/v1/alert-notifications/{item.id}/retry').status_code == 409


def test_smtp_uses_verified_tls_timeout_and_stable_message_id(client: TestClient, database_session: Session) -> None:
    _configured(client)
    item = _enqueue(database_session)
    channel = config.get_record(database_session)
    assert channel is not None
    smtp = Mock()
    smtp.send_message.return_value = {}
    with patch('src.services.alert_notification_delivery.smtplib.SMTP', return_value=smtp) as connect, \
            patch('src.services.alert_notification_delivery._resolve_host', return_value='192.0.2.20'):
        delivery.send_smtp(channel, item, client.app.state.credential_vault)
    assert connect.call_args.kwargs['timeout'] == delivery.SMTP_TIMEOUT_SECONDS
    assert connect.call_args.kwargs['local_hostname'] == 'linuxops.invalid'
    context = smtp.starttls.call_args.kwargs['context']
    assert context.check_hostname is True
    assert smtp.login.call_args.args == ('mailer', 'mail-secret-test')
    message = smtp.send_message.call_args.args[0]
    assert message['Message-ID'] == f'<{item.id}@linuxops.invalid>'
    assert 'private-value' not in message.get_content()


def test_partial_recipient_refusal_is_not_reported_sent(client: TestClient, database_session: Session) -> None:
    _configured(client)
    item = _enqueue(database_session)
    channel = config.get_record(database_session)
    smtp = Mock()
    smtp.send_message.return_value = {'admin@example.test': (550, b'secret server detail')}
    with patch('src.services.alert_notification_delivery.smtplib.SMTP', return_value=smtp), \
            patch('src.services.alert_notification_delivery._resolve_host', return_value='192.0.2.20'):
        with pytest.raises(delivery.NotificationDeliveryError, match='smtp_recipients_refused'):
            delivery.send_smtp(channel, item, client.app.state.credential_vault)


def test_config_model_does_not_accept_empty_password_overwrite() -> None:
    with pytest.raises(ValueError):
        NotificationConfigUpdate(enabled=False, password='')
