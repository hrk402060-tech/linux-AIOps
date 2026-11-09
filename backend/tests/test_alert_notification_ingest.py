import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, RootCauseAnalysis
from src.models.alert_notification import AlertNotification, AlertNotificationReceipt
from src.schemas.alert import AlertIngest
from src.services import alerts, alert_notifications, diagnostic_job_state


def _connector(client: TestClient) -> dict[str, object]:
    result = client.post("/api/v1/alert-connectors", json={
        "name": "Notification integration", "source_type": "prometheus",
    })
    assert result.status_code == 201
    return result.json()


def _asset(
    client: TestClient, name: str = "notification-node",
    host: str = "192.0.2.189",
) -> str:
    result = client.post("/api/v1/assets", json={
        "name": name, "host": host, "ssh_port": 22,
        "os_family": "ubuntu", "environment": "test", "group_name": "notify",
        "credential_id": None, "labels": [],
    })
    assert result.status_code == 201
    return result.json()["id"]


def _ingest(
    client: TestClient, connector: dict[str, object], asset_id: str,
    external_id: str = "integration-notification", nonce: str | None = None,
) -> Response:
    body = json.dumps({
        "external_id": external_id, "title": "High CPU password=never-mail-me",
        "description": "Bearer notification-secret", "severity": "high",
        "asset_id": asset_id,
    }).encode()
    timestamp, nonce = str(int(time.time())), nonce or uuid4().hex
    signature = hmac.new(
        str(connector["secret"]).encode(),
        timestamp.encode() + b"." + nonce.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers={
        "Content-Type": "application/json", "X-Alert-Timestamp": timestamp,
        "X-Alert-Nonce": nonce, "X-Alert-Signature": f"sha256={signature}",
    })


def test_alert_and_unauthorized_diagnosis_each_enqueue_once(client: TestClient, database_session: Session) -> None:
    connector, asset_id = _connector(client), _asset(client)
    with patch("src.services.alerts.ssh_host_key_registry.require_registered") as key:
        response = _ingest(client, connector, asset_id)
    assert response.status_code == 201
    key.assert_not_called()
    alert = response.json()
    assert alert["latest_analysis"]["error_code"] == "diagnostic_authorization_required"
    notifications = client.get(f"/api/v1/alerts/{alert['id']}/notifications").json()["items"]
    assert len(notifications) == 2
    assert {item["kind"] for item in notifications} == {"alert_received", "diagnostic_result"}
    assert all(item["status"] in {"blocked", "pending"} for item in notifications)
    assert all(item["attempts"] == 0 for item in notifications)
    summary = client.get(f"/api/v1/alerts/{alert['id']}").json()["notification_summary"]
    assert summary["total"] == 2 and summary["sent"] == 0
    assert _ingest(client, connector, asset_id).status_code == 200
    assert len(client.get(f"/api/v1/alerts/{alert['id']}/notifications").json()["items"]) == 2
    from test_diagnostic_grants import login_role
    login_role(client, database_session, 'operator')
    diagnosed = client.post(f"/api/v1/alerts/{alert['id']}/diagnostics", json={
        'confirmation': alert['id'], 'reason': 'manual rerun'})
    assert diagnosed.status_code == 202
    results = client.get(f"/api/v1/alerts/{alert['id']}/notifications").json()["items"]
    assert len(results) == 3
    assert sum(item["kind"] == "diagnostic_result" for item in results) == 2
    assert all(item["sent_at"] is None for item in results)
    current = client.get(f"/api/v1/alerts/{alert['id']}").json()
    latest = current["notification_summary"]["latest_diagnostic"]
    assert latest["analysis_id"] == current["latest_analysis"]["id"]
    assert latest["status"] in {"blocked", "pending"}


def test_terminal_alert_reopens_with_new_notification_and_diagnosis(
    client: TestClient, database_session: Session,
) -> None:
    connector, asset_id = _connector(client), _asset(client)
    first = _ingest(client, connector, asset_id, external_id="recurring-alert").json()
    record = database_session.get(Alert, first["id"])
    assert record is not None
    record.status = "closed"
    database_session.commit()

    reopened = _ingest(
        client, connector, asset_id, external_id="recurring-alert",
    )
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["id"] == first["id"]
    assert body["status"] == "open"
    assert body["occurrence_count"] == 2
    timeline = client.get(f"/api/v1/alerts/{first['id']}/timeline").json()
    assert any(item["event_type"] == "reopened" for item in timeline)
    assert sum(item["event_type"] == "diagnostic_queued" for item in timeline) == 2
    notifications = client.get(
        f"/api/v1/alerts/{first['id']}/notifications"
    ).json()["items"]
    assert sum(item["kind"] == "alert_received" for item in notifications) == 2
    assert sum(item["kind"] == "diagnostic_result" for item in notifications) == 2


def test_terminal_alert_recurrence_cannot_move_to_another_asset(
    client: TestClient, database_session: Session,
) -> None:
    connector, asset_id = _connector(client), _asset(client)
    first = _ingest(client, connector, asset_id, external_id="fixed-location").json()
    other_asset = _asset(client, "notification-node-2", "192.0.2.190")
    record = database_session.get(Alert, first["id"])
    assert record is not None
    record.status = "resolved"
    database_session.commit()

    response = _ingest(client, connector, other_asset, external_id="fixed-location")
    assert response.status_code == 409
    database_session.expire_all()
    record = database_session.get(Alert, first["id"])
    assert record is not None and record.status == "resolved"


def test_latest_diagnostic_notification_does_not_use_historical_result(
    client: TestClient, database_session: Session,
) -> None:
    connector, asset_id = _connector(client), _asset(client)
    alert = _ingest(client, connector, asset_id).json()
    analysis = RootCauseAnalysis(
        id=str(uuid4()), alert_id=alert["id"], asset_id=asset_id, status="completed",
        trigger_type="manual", policy_version="test", summary="new result", evidence={},
        error_code=None, operator="admin", trace_id=str(uuid4()),
        started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
    )
    database_session.add(analysis)
    database_session.flush()
    summary = alert_notifications.summarize(database_session, alert["id"], analysis.id)
    assert summary["diagnostic_results"] == 1
    assert summary["latest_diagnostic"] is None
    diagnostic_job_state.enqueue_diagnosis(database_session, database_session.get(
        Alert, alert["id"]), analysis)
    database_session.commit()
    summary = alert_notifications.summarize(database_session, alert["id"], analysis.id)
    assert summary["latest_diagnostic"]["analysis_id"] == analysis.id


def test_mapping_failure_notifies_and_preserves_nonce_before_corrected_ingest(
    client: TestClient,
) -> None:
    connector = _connector(client)
    missing_id, nonce = str(uuid4()), uuid4().hex
    with patch("src.services.alerts.ssh_host_key_registry.require_registered") as key:
        response = _ingest(client, connector, missing_id, nonce=nonce)
    assert response.status_code == 404
    key.assert_not_called()
    history = client.get("/api/v1/alert-notifications?limit=100").json()["items"]
    assert len(history) == 1
    assert history[0]["kind"] == "asset_mapping_failed"
    assert history[0]["alert_id"] is None
    assert _ingest(client, connector, missing_id, nonce=nonce).status_code == 409
    assert _ingest(client, connector, missing_id, "different-external", nonce).status_code == 409
    assert _ingest(client, connector, missing_id).status_code == 404
    assert client.get("/api/v1/alert-notifications").json()["total"] == 1
    asset_id = _asset(client)
    corrected = _ingest(client, connector, asset_id)
    assert corrected.status_code == 201
    rows = client.get("/api/v1/alert-notifications?limit=100").json()["items"]
    assert len(rows) == 3
    assert len([row for row in rows if row["kind"] == "asset_mapping_failed"]) == 1
    assert len(client.get("/api/v1/alerts").json()["items"]) == 1


def test_notification_permissions_and_pagination(client: TestClient) -> None:
    connector, asset_id = _connector(client), _asset(client)
    alert = _ingest(client, connector, asset_id).json()
    items = client.get(f"/api/v1/alerts/{alert['id']}/notifications").json()["items"]
    assert client.get("/api/v1/alert-notifications?limit=1").json()["total"] == 2
    assert len(client.get("/api/v1/alert-notifications?limit=1").json()["items"]) == 1
    assert client.get("/api/v1/alert-notifications?limit=101").status_code == 422
    assert client.get("/api/v1/alert-notifications?offset=-1").status_code == 422
    assert client.post(f"/api/v1/alert-notifications/{items[0]['id']}/acknowledge").status_code == 403
    assert client.get("/api/v1/alerts/nonexistent/notifications").status_code == 404
    client.headers.pop("Authorization")
    assert client.get("/api/v1/alert-notification-config").status_code == 401
    assert client.get("/api/v1/alert-notifications").status_code == 401
    assert client.post(f"/api/v1/alert-notifications/{items[0]['id']}/retry").status_code == 401


def test_notification_configuration_is_audited_without_password(client: TestClient) -> None:
    result = client.patch("/api/v1/alert-notification-config", json={
        "enabled": True, "host": "mail.example.test", "port": 465, "security": "ssl",
        "sender": "platform@example.test", "recipients": ["admin@example.test"],
        "username": "mailer", "password": "test-only-notification-password",
    })
    assert result.status_code == 200
    audit = client.get("/api/v1/audit-log?source=alert_notification").json()
    assert audit["total"] == 1
    assert audit["items"][0]["action"] == "configuration_updated"
    assert audit["items"][0]["operator"] == "break-glass-admin"
    assert "test-only-notification-password" not in json.dumps(audit)


def test_receipt_outbox_and_alert_roll_back_together(
    client: TestClient, database_session: Session,
) -> None:
    connector, asset_id = _connector(client), _asset(client)
    record = database_session.get(AlertConnector, connector["id"])
    assert record is not None
    payload = AlertIngest(external_id="atomic", title="Alert", severity="high", asset_id=asset_id)
    with patch.object(database_session, "commit", side_effect=SQLAlchemyError("commit failed")):
        with pytest.raises(SQLAlchemyError):
            alerts.ingest(database_session, record, payload, "atomic-nonce", client.app.state.credential_vault)
    assert database_session.scalar(select(Alert.id)) is None
    assert database_session.scalar(select(AlertNotification.id)) is None
    assert database_session.scalar(select(AlertNotificationReceipt.id)) is None


def test_finished_diagnosis_and_its_notification_are_atomic(
    client: TestClient, database_session: Session,
) -> None:
    connector, asset_id = _connector(client), _asset(client)
    alert_id = _ingest(client, connector, asset_id).json()["id"]
    record = database_session.get(Alert, alert_id)
    assert record is not None
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.services import diagnostic_jobs, diagnostic_job_claims, diagnostic_job_worker
    with patch('src.services.diagnostic_grants.require_grant', return_value=SimpleNamespace(revision=1)):
        diagnostic_jobs.enqueue(database_session, record, 'manual', 'admin', 'atomic finish test')
        database_session.commit()
        claim = diagnostic_job_claims.claim_next(database_session)
    with patch('src.services.diagnostic_job_state.enqueue_diagnosis', side_effect=SQLAlchemyError('outbox failed')):
        with pytest.raises(SQLAlchemyError):
            diagnostic_job_worker.finalize(client.app.state.session_factory, claim, {
                'status': 'completed', 'error_code': None, 'summary': 'atomic', 'evidence': {},
                'finished_at': datetime.now(timezone.utc)})
    database_session.expire_all()
    analyses = database_session.scalars(select(RootCauseAnalysis).where(RootCauseAnalysis.alert_id == alert_id)).all()
    assert len(analyses) == 2 and sum(item.status == 'running' for item in analyses) == 1
    notifications = database_session.scalars(select(AlertNotification).where(AlertNotification.alert_id == alert_id)).all()
    assert len(notifications) == 2
