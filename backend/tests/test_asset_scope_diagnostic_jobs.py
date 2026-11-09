from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.models.alert import Alert, RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.asset_access import UserAssetAccess
from src.models.auth import User
from src.models.diagnostic_job import DiagnosticJob
from src.services import diagnostic_job_claims, diagnostic_job_worker, diagnostic_jobs
from src.services.diagnostic_job_guard import guard
from src.services.alert_diagnostics import DiagnosticAuthorizationRequired
from test_alerts_api import _connector, _ingest
from test_diagnostic_grants import enroll, identity as identity_fixture, login_role


@pytest.fixture
def authorized(client: TestClient, database_session: Session) -> dict[str, object]:
    payload = enroll(client, database_session, identity_fixture.__wrapped__()[0])
    login_role(client, database_session, "admin")
    assert client.post("/api/v1/diagnostic-grants", json=payload).status_code == 201
    connector = _connector(client)
    body, headers = _ingest(client, connector, payload["asset_id"])
    with patch("src.services.alert_diagnostics.collect"):
        response = client.post(
            f"/api/v1/alert-ingest/{connector['id']}",
            content=body,
            headers=headers,
        )
    assert response.status_code == 201
    return response.json()


def _manual_job(
    client: TestClient, session: Session, alert: dict[str, object],
) -> DiagnosticJob:
    current = session.get(DiagnosticJob, alert["latest_job"]["id"])
    diagnostic_jobs.cancel(session, str(alert["id"]), current.id, "diagnostic-admin", "manual retry")
    login_role(client, session, "operator")
    response = client.post(f"/api/v1/alerts/{alert['id']}/diagnostics", json={
        "confirmation": alert["id"], "reason": "manual read-only investigation",
    })
    assert response.status_code == 202, response.text
    return session.get(DiagnosticJob, response.json()["id"])


def _revoke(session: Session, change: str) -> None:
    user = session.scalar(select(User).where(User.username == "diagnostic-operator"))
    if change == "asset":
        user.asset_scope = "assigned"
        session.execute(delete(UserAssetAccess).where(UserAssetAccess.user_id == user.id))
    elif change == "disabled":
        user.enabled = False
    elif change == "password":
        user.must_change_password = True
    else:
        user.role = "auditor"
    session.commit()


@pytest.mark.parametrize("change", ["asset", "disabled", "password", "role"])
def test_manual_job_revoked_before_claim_is_sealed_without_collection(
    client: TestClient, database_session: Session, authorized: dict[str, object],
    change: str,
) -> None:
    job = _manual_job(client, database_session, authorized)
    _revoke(database_session, change)
    with patch("src.services.alert_diagnostics.collect") as collect:
        assert diagnostic_job_claims.claim_next(database_session) is None
    collect.assert_not_called()
    database_session.expire_all()
    result = database_session.get(DiagnosticJob, job.id)
    assert result.status == "failed"
    assert result.last_error_code == "diagnostic_authorization_required"
    analysis = database_session.get(RootCauseAnalysis, result.current_analysis_id)
    assert analysis.evidence["execution_performed"] is False
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == analysis.id,
    )) is not None


def test_running_manual_job_rechecks_current_asset_permission(
    client: TestClient, database_session: Session, authorized: dict[str, object],
) -> None:
    _manual_job(client, database_session, authorized)
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    _revoke(database_session, "asset")
    with pytest.raises(DiagnosticAuthorizationRequired):
        guard(client.app.state.session_factory, claim)
    with patch("src.services.alert_diagnostics.collect") as collect:
        assert diagnostic_job_worker.execute_claim(
            client.app.state.session_factory, client.app.state.credential_vault, claim,
        )
    collect.assert_not_called()
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, claim.job_id).status == "failed"


def test_manual_result_cannot_finish_as_success_after_requester_revocation(
    client: TestClient, database_session: Session, authorized: dict[str, object],
) -> None:
    _manual_job(client, database_session, authorized)
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    _revoke(database_session, "asset")
    assert diagnostic_job_worker.finalize(client.app.state.session_factory, claim, {
        "status": "completed", "error_code": None, "summary": "unsafe success",
        "evidence": {"linux.uptime": "read before revocation"},
    })
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    analysis = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == "failed"
    assert analysis.error_code == "diagnostic_authorization_interrupted"
    assert "unsafe success" not in analysis.summary
    assert analysis.evidence["linux.uptime"] == "read before revocation"
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == analysis.id,
    )) is not None


def test_changed_alert_asset_cannot_redirect_a_queued_job(
    client: TestClient, database_session: Session, authorized: dict[str, object],
) -> None:
    other = client.post("/api/v1/assets", json={
        "name": "other-diagnostic-target", "host": "192.0.2.76",
    }).json()
    database_session.execute(update(Alert).where(Alert.id == authorized["id"]).values(
        asset_id=other["id"], asset_name=other["name"],
    ))
    database_session.commit()
    with patch("src.services.alert_diagnostics.collect") as collect:
        assert diagnostic_job_claims.claim_next(database_session) is None
    collect.assert_not_called()
    database_session.expire_all()
    assert database_session.get(
        DiagnosticJob, authorized["latest_job"]["id"],
    ).status == "failed"


def test_automatic_job_is_not_bound_to_unrelated_user_scope(
    client: TestClient, database_session: Session, authorized: dict[str, object],
) -> None:
    login_role(client, database_session, "operator")
    _revoke(database_session, "asset")
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    guard(client.app.state.session_factory, claim)
