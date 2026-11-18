from datetime import timedelta
from unittest.mock import Mock

import pytest

from src.models.alert_notification import AlertNotification
from src.services import alert_notification_delivery as delivery
from src.services import alert_notifications, notification_channels
from test_alert_notification_worker import _setup


def test_expired_or_replaced_claim_cannot_acknowledge_channel(client, database_session) -> None:
    identifier = _setup(client, database_session)
    item, token = delivery.claim_next(database_session)
    with pytest.raises(ValueError, match="notification_claim_lost"):
        notification_channels.acknowledge_channel(database_session, identifier, "email", "stale-token")
    assert notification_channels.delivery_receipts(database_session, identifier) == {}
    item.lease_expires_at = alert_notifications.utc_now() - timedelta(seconds=1)
    database_session.commit()
    with pytest.raises(ValueError, match="notification_claim_lost"):
        notification_channels.acknowledge_channel(database_session, identifier, "email", token)
    assert notification_channels.delivery_receipts(database_session, identifier) == {}


def test_both_channels_retry_only_unsuccessful_channel(client, database_session, monkeypatch) -> None:
    identifier = _setup(client, database_session)
    settings = client.get("/api/v1/system/settings").json()["settings"]["notifications"]
    assert client.put("/api/v1/system/settings/notifications", json={
        **settings, "default_channel": "both",
    }).status_code == 200
    assert client.put("/api/v1/system/notification-channel", json={
        "enabled": True, "url": "https://notify.example.test/hook",
    }).status_code == 200
    mail = Mock()
    webhook = Mock(side_effect=[ValueError("webhook_delivery_failed"), None])
    monkeypatch.setattr(delivery, "send_smtp", mail)
    monkeypatch.setattr(notification_channels, "send", webhook)
    factory, vault = client.app.state.session_factory, client.app.state.credential_vault
    assert delivery.deliver_batch(factory, vault) == 1
    database_session.expire_all()
    item = database_session.get(AlertNotification, identifier)
    assert item.status == "retry"
    assert notification_channels.delivery_receipts(database_session, identifier) == {"email": True}
    item.next_attempt_at = alert_notifications.utc_now()
    database_session.commit()
    assert delivery.deliver_batch(factory, vault) == 1
    database_session.expire_all()
    assert database_session.get(AlertNotification, identifier).status == "sent"
    assert mail.call_count == 1 and webhook.call_count == 2


def test_lost_claim_during_email_cannot_acknowledge_or_send_second_channel(
    client, database_session, monkeypatch,
) -> None:
    identifier = _setup(client, database_session)
    settings = client.get("/api/v1/system/settings").json()["settings"]["notifications"]
    assert client.put("/api/v1/system/settings/notifications", json={
        **settings, "default_channel": "both",
    }).status_code == 200
    assert client.put("/api/v1/system/notification-channel", json={
        "enabled": True, "url": "https://notify.example.test/hook",
    }).status_code == 200
    factory = client.app.state.session_factory

    def lose_lease(*_: object) -> None:
        with factory() as session:
            item = session.get(AlertNotification, identifier)
            item.claim_token = "replacement-worker-token"
            session.commit()

    webhook = Mock()
    monkeypatch.setattr(delivery, "send_smtp", lose_lease)
    monkeypatch.setattr(notification_channels, "send", webhook)
    assert delivery.deliver_batch(factory, client.app.state.credential_vault) == 1
    webhook.assert_not_called()
    database_session.expire_all()
    assert notification_channels.delivery_receipts(database_session, identifier) == {}
    assert database_session.get(AlertNotification, identifier).status == "sending"


def test_disabling_during_email_pauses_webhook_without_repeating_email(
    client, database_session, monkeypatch,
) -> None:
    identifier = _setup(client, database_session)
    policy = client.get("/api/v1/system/settings").json()["settings"]["notifications"]
    assert client.put("/api/v1/system/settings/notifications", json={
        **policy, "default_channel": "both",
    }).status_code == 200
    assert client.put("/api/v1/system/notification-channel", json={
        "enabled": True, "url": "https://notify.example.test/hook",
    }).status_code == 200
    from src.services import platform_settings

    factory, vault = client.app.state.session_factory, client.app.state.credential_vault
    def disable_after_mail(*_: object) -> None:
        with factory() as session:
            platform_settings.save_category(session, client.app.state.runtime_settings,
                                            "notifications", {**policy, "default_channel": "both",
                                                              "enabled": False}, "admin", None)
    mail, webhook = Mock(side_effect=disable_after_mail), Mock()
    monkeypatch.setattr(delivery, "send_smtp", mail)
    monkeypatch.setattr(notification_channels, "send", webhook)
    delivery.deliver_batch(factory, vault)
    webhook.assert_not_called()
    database_session.expire_all()
    item = database_session.get(AlertNotification, identifier)
    assert item.status == "blocked" and item.attempts == 0
    assert notification_channels.delivery_receipts(database_session, identifier) == {"email": True}
    assert client.put("/api/v1/system/settings/notifications", json={
        **policy, "default_channel": "both", "enabled": True,
    }).status_code == 200
    assert delivery.deliver_batch(factory, vault) == 1
    database_session.expire_all()
    assert database_session.get(AlertNotification, identifier).status == "sent"
    assert mail.call_count == 1 and webhook.call_count == 1
