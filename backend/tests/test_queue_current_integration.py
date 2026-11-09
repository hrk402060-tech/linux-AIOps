from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.alert import RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_job import DiagnosticJob
from test_alerts_api import _asset, _connector, _ingest
from test_diagnostic_jobs import authorized  # noqa: F401


def assert_cleanup_blocked(client: TestClient, asset_id: str) -> None:
    preview = client.get(f"/api/v1/assets/{asset_id}/cleanup-preview")
    assert preview.status_code == 200 and preview.json()["allowed"] is False
    assert client.delete(f"/api/v1/assets/{asset_id}").status_code == 409
    response = client.post(f"/api/v1/assets/{asset_id}/cleanup", json={
        "confirmation": f"DELETE {asset_id}", "reason": "isolated compatibility validation",
    })
    assert response.status_code == 409


def test_no_grant_failed_queue_preserves_evidence_and_prevents_asset_cleanup(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client, environment="test")
    connector = _connector(client)
    body, headers = _ingest(client, connector, asset["id"])
    with patch("src.services.alert_diagnostics.collect") as network:
        response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
        network.assert_not_called()
    assert response.status_code == 201
    alert = response.json()
    assert alert["latest_job"]["status"] == "failed"
    assert alert["latest_job"]["last_error_code"] == "diagnostic_authorization_required"
    assert_cleanup_blocked(client, asset["id"])
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, alert["latest_job"]["id"]) is not None
    assert database_session.scalar(select(RootCauseAnalysis).where(
        RootCauseAnalysis.job_id == alert["latest_job"]["id"],
    )) is not None
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.alert_id == alert["id"],
        AlertNotification.kind == "diagnostic_result",
    )) is not None


def test_authorized_queue_keeps_manual_cleanup_blocked(
    client: TestClient, database_session: Session, authorized: dict[str, object],  # noqa: F811
) -> None:
    assert_cleanup_blocked(client, authorized["asset_id"])
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, authorized["latest_job"]["id"])
    assert job is not None and job.status == "queued" and job.attempt_count == 0


def test_queue_app_preserves_ai_configuration_and_recovery_routes(
    client: TestClient, database_session: Session,
) -> None:
    settings = client.get("/api/v1/agent/settings/terminal")
    assert settings.status_code == 200 and settings.json()["api_key_masked"] is None
    assert client.get("/api/v1/agent").status_code == 200
    assert client.get("/api/v1/scan-tasks?archive_view=all").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/alerts/{alert_id}/diagnostic-jobs" in paths
    assert "/api/v1/alerts/{alert_id}/recovery-verifications" in paths
