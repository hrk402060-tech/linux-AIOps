import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertTimeline, RootCauseAnalysis
from src.models.alert_ingest_receipt import AlertIngestReceipt
from src.models.alert_notification import AlertNotification, AlertNotificationReceipt
from src.models.diagnostic_job import DiagnosticJob
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.services.alert_security import sign_request


def create_asset(client: TestClient, name: str = "source-node") -> str:
    response = client.post("/api/v1/assets", json={
        "name": name, "host": "192.0.2.171", "os_family": "ubuntu",
        "environment": "test",
    })
    assert response.status_code == 201
    return str(response.json()["id"])


def create_connector(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/alert-connectors", json={
        "name": "Source status connector", "source_type": "generic_webhook",
    })
    assert response.status_code == 201
    return response.json()


def send(
    client: TestClient, connector: dict[str, str], asset_id: str,
    status: str = "firing", *, nonce: str | None = None,
    external_id: str = "source-alert", host: str | None = None,
) -> Response:
    payload = {
        "external_id": external_id, "title": "Source alert",
        "severity": "high", "asset_id": asset_id, "source_status": status,
        **({"asset_host": host} if host else {}),
    }
    body, headers = sign_request(connector["secret"], payload, nonce=nonce)
    return client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )


def counts(session: Session) -> dict[str, int]:
    return {
        model.__tablename__: int(session.scalar(
            select(func.count()).select_from(model),
        ) or 0)
        for model in (
            Alert, DiagnosticJob, RootCauseAnalysis, AlertNotification,
            AlertNotificationReceipt, RecoveryVerification, RecoveryEvaluation,
        )
    }


@pytest.mark.parametrize("human_status", [
    "open", "acknowledged", "in_progress", "resolved", "closed",
])
def test_source_resolved_only_records_signal_without_changing_human_workflow(
    client: TestClient, database_session: Session, human_status: str,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    created = send(client, connector, asset_id)
    assert created.status_code == 201
    alert_id = created.json()["id"]
    stored = database_session.get(Alert, alert_id)
    assert stored is not None
    stored.status = human_status
    database_session.commit()
    before, original_payload = counts(database_session), dict(stored.sanitized_payload)
    received_at, updated_at = stored.received_at, stored.updated_at
    with patch("src.services.alerts.diagnostic_jobs.enqueue") as enqueue:
        response = send(client, connector, asset_id, "resolved")
    assert response.status_code == 200
    assert response.json()["status"] == human_status
    assert response.json()["occurrence_count"] == 1
    assert response.json()["latest_recovery"] is None
    enqueue.assert_not_called()
    database_session.expire_all()
    assert counts(database_session) == before
    stored = database_session.get(Alert, alert_id)
    assert stored is not None
    assert stored.sanitized_payload == original_payload
    assert stored.received_at.replace(tzinfo=None) == received_at.replace(tzinfo=None)
    assert stored.updated_at.replace(tzinfo=None) == updated_at.replace(tzinfo=None)
    signal = database_session.scalar(select(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == "source_resolved",
    ))
    assert signal is not None and signal.status == human_status
    assert signal.details == {"source_status": "resolved", "recovery_verified": False}
    assert "恢复信号" in signal.note


def test_source_resolved_consumes_nonce_without_sending_notification(
    client: TestClient, database_session: Session,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    alert_id = send(client, connector, asset_id).json()["id"]
    before = counts(database_session)
    nonce = "resolved-signal-replay-nonce"
    assert send(client, connector, asset_id, "resolved", nonce=nonce).status_code == 200
    assert send(client, connector, asset_id, "resolved", nonce=nonce).status_code == 409
    assert counts(database_session) == before
    signals = database_session.scalars(select(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == "source_resolved",
    )).all()
    assert len(signals) == 1
    receipts = database_session.scalars(select(AlertIngestReceipt).where(
        AlertIngestReceipt.connector_id == connector["id"],
    )).all()
    assert [item.result for item in receipts].count("success") == 2
    assert [item.error_code for item in receipts].count("replay_detected") == 1


def test_unknown_source_resolved_does_not_create_alert_or_notification(
    client: TestClient, database_session: Session,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    response = send(client, connector, asset_id, "resolved")
    assert response.status_code == 404
    assert not any(counts(database_session).values())
    assert database_session.scalar(select(func.count()).select_from(AlertTimeline)) == 0


@pytest.mark.parametrize("different_asset", [False, True])
def test_source_resolved_rejects_ambiguous_or_changed_asset(
    client: TestClient, database_session: Session, different_asset: bool,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    alert_id = send(client, connector, asset_id).json()["id"]
    other_id = create_asset(client, "another-source-node")
    before = counts(database_session)
    response = send(
        client, connector, other_id if different_asset else asset_id, "resolved",
        host=None if different_asset else "192.0.2.171",
    )
    assert response.status_code == 409
    assert counts(database_session) == before
    assert database_session.scalar(select(func.count()).select_from(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == "source_resolved",
    )) == 0


def test_source_resolved_still_requires_valid_signature(
    client: TestClient, database_session: Session,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    send(client, connector, asset_id)
    before = counts(database_session)
    payload = {"external_id": "source-alert", "title": "Signal", "severity": "low",
               "asset_id": asset_id, "source_status": "resolved"}
    body, headers = sign_request(connector["secret"], payload)
    headers["X-Alert-Signature"] = "sha256=" + "0" * 64
    response = client.post(
        f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers,
    )
    assert response.status_code == 401
    assert counts(database_session) == before


def test_source_resolved_timeline_and_runtime_receipt_commit_atomically(
    client: TestClient, database_session: Session,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    alert_id = send(client, connector, asset_id).json()["id"]
    engine = database_session.get_bind()

    def reject_success(_conn, _cursor, statement, _params, _context, _many) -> None:
        if statement.startswith("INSERT INTO alert_ingest_receipts"):
            raise SQLAlchemyError("source receipt unavailable")

    event.listen(engine, "before_cursor_execute", reject_success)
    try:
        response = send(client, connector, asset_id, "resolved")
    finally:
        event.remove(engine, "before_cursor_execute", reject_success)
    assert response.status_code == 503
    assert database_session.scalar(select(func.count()).select_from(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == "source_resolved",
    )) == 0


def test_later_firing_still_reopens_manually_closed_alert(
    client: TestClient, database_session: Session,
) -> None:
    asset_id, connector = create_asset(client), create_connector(client)
    first = send(client, connector, asset_id).json()
    stored = database_session.get(Alert, first["id"])
    assert stored is not None
    stored.status = "closed"
    database_session.commit()
    assert send(client, connector, asset_id, "resolved").json()["status"] == "closed"
    response = send(client, connector, asset_id)
    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["occurrence_count"] == 2
    assert response.json()["id"] == first["id"]
    assert "resolved" in json.dumps(client.get(
        f"/api/v1/alerts/{first['id']}/timeline",
    ).json())
