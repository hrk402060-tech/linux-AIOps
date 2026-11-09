from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector
from src.services import alert_notifications
from test_user_asset_scope_alerts import _asset, _grant, _sign_in, _user


def _alert(session: Session, connector_id: str, asset_id: str, name: str) -> Alert:
    item = Alert(
        id=str(uuid4()),
        connector_id=connector_id,
        external_id=name,
        title=name,
        description="scope",
        severity="high",
        status="open",
        asset_id=asset_id,
        asset_name=name,
        sanitized_payload={},
        received_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    return item


def test_notification_list_hides_hidden_asset_diagnostic_results(
    client: TestClient, database_session: Session,
) -> None:
    connector = AlertConnector(
        id=str(uuid4()),
        name="diagnostic-notification-scope",
        source_type="generic",
        status="active",
        encrypted_secret=b"unused",
        nonce=b"0" * 12,
        key_version="test",
        secret_hint="test",
        operator="system",
    )
    database_session.add(connector)
    database_session.commit()
    allowed_asset = _asset(client, "notification-allowed")
    hidden_asset = _asset(client, "notification-hidden")
    allowed = _alert(database_session, connector.id, allowed_asset, "allowed")
    hidden = _alert(database_session, connector.id, hidden_asset, "hidden")
    alert_notifications.enqueue(
        database_session, "diagnostic-notice-allowed", "diagnostic_result",
        allowed.id, "analysis-allowed", "allowed", "allowed",
    )
    alert_notifications.enqueue(
        database_session, "diagnostic-notice-hidden", "diagnostic_result",
        hidden.id, "analysis-hidden", "hidden", "hidden",
    )
    database_session.commit()

    user = _user(client)
    _grant(client, user, allowed_asset)
    _sign_in(client, user)

    response = client.get("/api/v1/alert-notifications")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["alert_id"] == allowed.id
