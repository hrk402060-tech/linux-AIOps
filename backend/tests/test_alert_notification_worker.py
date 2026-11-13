from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import pytest

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.services import alert_notification_config as config
from src.services import alert_notification_delivery as delivery
from src.services import alert_notifications as notifications


def _setup(client: TestClient, session: Session) -> str:
    assert client.patch('/api/v1/alert-notification-config', json={
        'enabled': True, 'host': 'smtp.example.test', 'port': 465, 'security': 'ssl',
        'sender': 'ops@example.test', 'recipients': ['admin@example.test'],
    }).status_code == 200
    item = notifications.enqueue(session, 'worker-test', 'asset_mapping_failed', None, None, 'Alert', 'Body')
    session.commit()
    return item.id


def test_two_workers_can_only_claim_one_row(client: TestClient, database_session: Session) -> None:
    item_id = _setup(client, database_session)

    def claim() -> tuple[str, str] | None:
        with client.app.state.session_factory() as session:
            claimed = delivery.claim_next(session)
            return (claimed[0].id, claimed[1]) if claimed else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    successful = [item for item in results if item is not None]
    assert len(successful) == 1 and successful[0][0] == item_id


def test_live_claim_cannot_be_reset_by_retry_or_disable(client: TestClient, database_session: Session) -> None:
    item_id = _setup(client, database_session)
    claim = delivery.claim_next(database_session)
    assert claim is not None
    assert client.post(f'/api/v1/alert-notifications/{item_id}/retry').status_code == 409
    assert client.patch('/api/v1/alert-notification-config', json={'enabled': False}).status_code == 200
    database_session.refresh(claim[0])
    assert claim[0].status == 'sending' and claim[0].claim_token == claim[1]
    delivery._block_claim(database_session, item_id, claim[1])
    database_session.refresh(claim[0])
    assert claim[0].status == 'blocked' and claim[0].attempts == 0


def test_missing_or_disabled_config_consumes_no_send_attempts(client: TestClient, database_session: Session) -> None:
    item_id = _setup(client, database_session)
    assert client.patch('/api/v1/alert-notification-config', json={'enabled': False}).status_code == 200
    with patch('src.services.alert_notification_delivery.send_smtp') as send:
        assert delivery.deliver_batch(client.app.state.session_factory, client.app.state.credential_vault) == 0
    item = notifications.get_notification(database_session, item_id)
    database_session.refresh(item)
    assert item.status == 'blocked' and item.attempts == 0
    send.assert_not_called()


@pytest.mark.parametrize('configured', [False, True])
def test_retry_and_worker_preserve_configuration_block_reason(
    client: TestClient, database_session: Session, configured: bool,
) -> None:
    if configured:
        assert client.patch('/api/v1/alert-notification-config', json={'enabled': False}).status_code == 200
    item = notifications.enqueue(database_session, 'blocked-config', 'asset_mapping_failed', None, None, 'Alert', 'Body')
    item.status = 'pending'
    database_session.commit()
    assert delivery.claim_next(database_session) is None
    database_session.refresh(item)
    expected = 'notification_disabled' if configured else 'notification_not_configured'
    assert item.error_code == expected and item.attempts == 0
    response = client.post(f'/api/v1/alert-notifications/{item.id}/retry')
    assert response.status_code == 200
    assert response.json()['error_code'] == expected
    assert response.json()['attempts'] == 0


def test_ssl_verifies_dns_hostname_with_bounded_socket(client: TestClient, database_session: Session) -> None:
    item_id = _setup(client, database_session)
    claim = delivery.claim_next(database_session)
    assert claim is not None and claim[0].id == item_id
    channel = config.get_record(database_session)
    assert channel is not None
    smtp = Mock()
    smtp.send_message.return_value = {}
    with patch('src.services.alert_notification_delivery.smtplib.SMTP_SSL', return_value=smtp) as connect, \
            patch('src.services.alert_notification_delivery._resolve_host', return_value='192.0.2.20'):
        delivery.send_smtp(channel, claim[0], client.app.state.credential_vault)
    assert connect.call_args.kwargs['context'].check_hostname is True
    assert connect.call_args.kwargs['timeout'] == delivery.SMTP_TIMEOUT_SECONDS
    assert connect.call_args.kwargs['local_hostname'] == 'linuxops.invalid'
    assert smtp._host == 'smtp.example.test'
    smtp.connect.assert_called_once_with('192.0.2.20', 465)
    smtp.starttls.assert_not_called()
    assert delivery.SMTP_TOTAL_TIMEOUT_SECONDS + delivery.SMTP_TIMEOUT_SECONDS < delivery.LEASE_SECONDS


def test_global_history_bounded_and_no_snapshots_or_secrets_returned(client: TestClient, database_session: Session) -> None:
    _setup(client, database_session)
    response = client.get('/api/v1/alert-notifications?limit=100')
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert 'body' not in item and 'subject' not in item and 'claim_token' not in item
    assert client.get('/api/v1/alert-notifications?limit=101').status_code == 422
    assert client.get('/api/v1/alerts/missing/notifications').status_code == 404


def test_notification_anonymous_routes_rejected(client: TestClient) -> None:
    client.headers.pop('Authorization', None)
    assert client.get('/api/v1/alert-notifications').status_code == 401
    assert client.get('/api/v1/alert-notification-config').status_code == 401
    assert client.post('/api/v1/alert-notifications/missing/retry').status_code == 401
    assert client.post('/api/v1/alert-notifications/missing/acknowledge').status_code == 401


def test_shutdown_stops_batch_before_second_send(client: TestClient, database_session: Session) -> None:
    from threading import Event

    _setup(client, database_session)
    notifications.enqueue(database_session, 'worker-test-two', 'asset_mapping_failed', None, None, 'Alert two', 'Body')
    database_session.commit()
    stop = Event()

    def send_and_stop(*_: object) -> None:
        stop.set()

    with patch('src.services.alert_notification_delivery.send_smtp', side_effect=send_and_stop) as send:
        assert delivery.deliver_batch(client.app.state.session_factory, client.app.state.credential_vault, stop) == 1
    assert send.call_count == 1
    items = notifications.list_notifications(database_session, 0, 100)['items']
    assert {item['status'] for item in items} == {'sent', 'pending'}


def test_scheduler_stop_signals_inflight_worker_and_exits_bounded(client: TestClient) -> None:
    import asyncio
    from threading import Event

    entered = Event()

    def blocked_batch(_factory: object, _vault: object, stop: Event) -> int:
        entered.set()
        assert stop.wait(1)
        return 0

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(delivery.run_scheduler(client.app.state.session_factory, client.app.state.credential_vault, stop))
        assert await asyncio.to_thread(entered.wait, 1)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    with patch('src.services.alert_notification_delivery.deliver_batch', side_effect=blocked_batch):
        asyncio.run(run())


@pytest.mark.parametrize('security', ['starttls', 'ssl'])
def test_real_smtp_constructor_avoids_unbounded_local_dns(
    client: TestClient, database_session: Session, security: str,
) -> None:
    item_id = _setup(client, database_session)
    channel = config.get_record(database_session)
    assert channel is not None
    channel.security = security
    item = notifications.get_notification(database_session, item_id)
    item.recipients = ['admin@example.test']
    with patch('socket.getfqdn', side_effect=AssertionError('unbounded local DNS')) as resolve, \
            patch('src.services.alert_notification_delivery._resolve_host', return_value='192.0.2.20'), \
            patch('smtplib.SMTP.connect', return_value=(220, b'OK')), \
            patch('smtplib.SMTP.ehlo', return_value=(250, b'OK')), \
            patch('smtplib.SMTP.starttls', return_value=(220, b'OK')), \
            patch('smtplib.SMTP.send_message', return_value={}), \
            patch('smtplib.SMTP.quit', return_value=(221, b'Bye')):
        delivery.send_smtp(channel, item, client.app.state.credential_vault)
    resolve.assert_not_called()
