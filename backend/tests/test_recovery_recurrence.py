from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector, AlertTimeline
from src.models.alert_notification import AlertNotification
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.schemas.alert import AlertIngest
from src.services import alerts, recovery
from test_recovery_api import login, recovery_alert as alert_fixture, start


@pytest.fixture
def recovery_alert(client: TestClient, database_session: Session) -> Alert:
    return alert_fixture.__wrapped__(client, database_session)


def reopen(client: TestClient, session: Session, alert: Alert, at: datetime) -> None:
    alert.status = "resolved"
    session.commit()
    connector = session.get(AlertConnector, alert.connector_id)
    payload = AlertIngest(
        external_id=alert.external_id, title="同一告警复发", severity="high",
        asset_id=alert.asset_id, description="重新定位本次异常",
    )
    with patch("src.services.alerts.datetime") as clock:
        clock.now.return_value = at
        result, created = alerts.ingest(
            session, connector, payload, uuid4().hex * 2, client.app.state.credential_vault,
        )
    assert result.id == alert.id and not created
    session.expire_all()


def test_reopened_alert_never_inherits_success_even_when_timestamps_match(
    client: TestClient, database_session: Session, recovery_alert: Alert,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = datetime.now(timezone.utc)
    monkeypatch.setattr(recovery, "utc_now", lambda: origin)
    login(client, database_session)
    old = start(client, recovery_alert)
    row = database_session.get(RecoveryVerification, old["id"])
    row.status, row.active_alert_id, row.finished_at = "verified", None, origin
    database_session.commit()
    reopen(client, database_session, recovery_alert, origin)
    current = client.get(f"/api/v1/alerts/{recovery_alert.id}").json()
    assert current["status"] == "open" and current["latest_recovery"] is None
    history = client.get(f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications").json()
    assert history["items"][0]["status"] == "verified"
    assert history["items"][0]["is_current"] is False
    for status in ("acknowledged", "in_progress"):
        alerts.update_status(database_session, recovery_alert.id, status, "人工重新处理本轮", "recovery-operator")
    new = start(client, recovery_alert)
    assert new["is_current"] is True and new["started_at"] == old["started_at"]
    assert client.get(f"/api/v1/alerts/{recovery_alert.id}").json()["latest_recovery"]["id"] == new["id"]
    reopen(client, database_session, recovery_alert, origin)
    second = client.get(f"/api/v1/alerts/{recovery_alert.id}").json()
    assert second["latest_recovery"] is None
    history = client.get(f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications").json()
    assert all(not item["is_current"] for item in history["items"])


def test_recurrence_seals_active_observation_notifies_and_rejects_old_write(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    old = start(client, recovery_alert)
    reopen(client, database_session, recovery_alert, datetime.now(timezone.utc))
    row = database_session.get(RecoveryVerification, old["id"])
    assert row.status == "cancelled" and row.version == 2
    assert row.active_alert_id is None and row.finished_at is not None
    evaluation = database_session.scalar(select(RecoveryEvaluation).where(
        RecoveryEvaluation.verification_id == old["id"],
    ))
    assert evaluation.reason_code == "alert_reopened" and evaluation.operator == "system"
    notification = database_session.scalar(select(AlertNotification).where(
        AlertNotification.recovery_evaluation_id == evaluation.id,
    ))
    assert notification is not None and notification.status == "blocked"
    assert database_session.scalar(select(func.count()).select_from(AlertTimeline).where(
        AlertTimeline.alert_id == recovery_alert.id, AlertTimeline.event_type == "recovery_cancelled",
    )) == 1
    before = database_session.scalar(select(func.count()).select_from(RecoveryEvaluation))
    for action in ("evaluate", "cancel"):
        result = client.post(
            f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications/{old['id']}/{action}",
            json={"expected_version": 2, "confirmation": recovery_alert.id, "reason": "旧页面迟到操作"},
        )
        assert result.status_code == 409
    assert database_session.scalar(select(func.count()).select_from(RecoveryEvaluation)) == before
    history = client.get(f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications").json()
    assert history["options"]["active_verification"] is False
    assert history["items"][0]["is_current"] is False


def test_recurrence_rolls_back_cancellation_and_notification_together(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    old = start(client, recovery_alert)
    recovery_alert.status = "resolved"
    database_session.commit()
    notices = database_session.scalar(select(func.count()).select_from(AlertNotification))
    with patch("src.services.recovery._notify", side_effect=SQLAlchemyError("isolated failure")):
        with pytest.raises(SQLAlchemyError):
            reopen(client, database_session, recovery_alert, datetime.now(timezone.utc))
    database_session.rollback()
    database_session.expire_all()
    assert database_session.get(Alert, recovery_alert.id).status == "resolved"
    row = database_session.get(RecoveryVerification, old["id"])
    assert row.status == "observing" and row.version == 1
    assert row.active_alert_id == recovery_alert.id
    assert database_session.scalar(select(func.count()).select_from(RecoveryEvaluation)) == 0
    assert database_session.scalar(select(func.count()).select_from(AlertNotification)) == notices


def test_stale_session_cannot_close_an_alert_after_recurrence(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    recovery_alert.status = "resolved"
    database_session.commit()
    with client.app.state.session_factory() as stale:
        old = stale.get(Alert, recovery_alert.id)
        assert old.status == "resolved"
        reopen(client, database_session, recovery_alert, datetime.now(timezone.utc))
        with pytest.raises(alerts.AlertConflictError):
            alerts.update_status(stale, old.id, "closed", "旧页面关闭请求", "operator")
        stale.rollback()
    database_session.expire_all()
    assert database_session.get(Alert, recovery_alert.id).status == "open"


@pytest.mark.parametrize("offset,current", [(0, False), (1, True)])
def test_legacy_unbound_records_use_a_conservative_recurrence_boundary(
    client: TestClient, database_session: Session, recovery_alert: Alert,
    monkeypatch: pytest.MonkeyPatch, offset: int, current: bool,
) -> None:
    login(client, database_session)
    boundary = datetime.now(timezone.utc)
    reopen(client, database_session, recovery_alert, boundary)
    recovery_alert.status = "in_progress"
    database_session.commit()
    monkeypatch.setattr(recovery, "utc_now", lambda: boundary + timedelta(seconds=offset))
    item = start(client, recovery_alert)
    row = database_session.get(RecoveryVerification, item["id"])
    row.config_snapshot = {key: value for key, value in row.config_snapshot.items() if key != "alert_cycle_id"}
    database_session.commit()
    history = client.get(f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications").json()
    assert history["items"][0]["is_current"] is current
    latest = client.get(f"/api/v1/alerts/{recovery_alert.id}").json()["latest_recovery"]
    assert (latest is not None) is current
