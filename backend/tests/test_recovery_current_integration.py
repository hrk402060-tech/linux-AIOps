from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.asset import Asset
from src.models.credential import Credential
from src.services import recovery
from test_recovery_api import (
    evaluate, login, recovery_alert as alert_fixture, samples, start,
)


@pytest.fixture
def recovery_alert(client: TestClient, database_session: Session) -> Alert:
    return alert_fixture.__wrapped__(client, database_session)


@pytest.mark.parametrize("changes,expected", [
    ({"host": "192.0.2.11"}, 409),
    ({"ssh_port": 2222}, 409),
    ({"host": "192.0.2.10", "ssh_port": 22}, 200),
    ({"name": "DEMO renamed"}, 200),
])
def test_active_recovery_guards_target_changes(
    client: TestClient, database_session: Session, recovery_alert: Alert,
    changes: dict[str, object], expected: int,
) -> None:
    login(client, database_session)
    start(client, recovery_alert)
    response = client.patch(f"/api/v1/assets/{recovery_alert.asset_id}", json=changes)
    assert response.status_code == expected
    if expected == 409:
        assert "恢复验证" in response.json()["detail"]
    database_session.expire_all()
    asset = database_session.get(Asset, recovery_alert.asset_id)
    assert asset.host == "192.0.2.10" and asset.ssh_port == 22


def test_cancel_releases_target_guard_without_remote_collection(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    with patch("src.services.monitoring.collect_asset") as collect, patch("src.services.probes.run_probe") as probe:
        item = start(client, recovery_alert)
        response = client.post(
            f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications/{item['id']}/cancel",
            json={"expected_version": 1, "confirmation": f" {recovery_alert.id} ", "reason": "更换目标前取消"},
        )
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        assert client.patch(f"/api/v1/assets/{recovery_alert.asset_id}", json={"host": "192.0.2.11"}).status_code == 200
        collect.assert_not_called()
        probe.assert_not_called()


def test_terminal_evaluation_flushes_before_notification_reference(
    client: TestClient, database_session: Session, recovery_alert: Alert, monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = datetime(2026, 10, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(recovery, "utc_now", lambda: origin)
    login(client, database_session)
    item = start(client, recovery_alert)
    samples(database_session, recovery_alert, origin)
    monkeypatch.setattr(recovery, "utc_now", lambda: origin + timedelta(seconds=60))
    statements: list[str] = []

    def capture(connection: object, cursor: object, statement: str, parameters: object,
                context: object, executemany: bool) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO"):
            statements.append(statement)

    engine = database_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = evaluate(client, recovery_alert, item)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200 and response.json()["status"] == "verified"
    evaluation = next(i for i, value in enumerate(statements) if "INSERT INTO recovery_evaluations" in value)
    notification = next(i for i, value in enumerate(statements) if "INSERT INTO alert_notifications" in value)
    assert evaluation < notification


def test_paginated_activity_and_cross_alert_evaluation_are_authoritative(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    item = start(client, recovery_alert)
    history = client.get(
        f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications?offset=999&limit=1",
    ).json()
    assert history["items"] == [] and history["total"] == 1
    assert history["options"]["active_verification"] is True
    other = Alert(
        id=str(uuid4()), connector_id=recovery_alert.connector_id,
        external_id="DEMO other recovery", title="DEMO other", description="", severity="high",
        status="in_progress", asset_id=recovery_alert.asset_id,
        asset_name=recovery_alert.asset_name, sanitized_payload={},
    )
    database_session.add(other)
    database_session.commit()
    result = client.post(
        f"/api/v1/alerts/{other.id}/recovery-verifications/{item['id']}/evaluate",
        json={"expected_version": 1, "confirmation": other.id, "reason": "错误关联隔离测试"},
    )
    assert result.status_code == 404


@pytest.mark.parametrize("role", ("admin", "operator"))
def test_real_human_cancel_accepts_trimmed_confirmation(
    client: TestClient, database_session: Session, recovery_alert: Alert, role: str,
) -> None:
    login(client, database_session, role)
    item = start(client, recovery_alert)
    url = f"/api/v1/alerts/{recovery_alert.id}/recovery-verifications/{item['id']}/cancel"
    payload = {"expected_version": 1, "confirmation": "wrong", "reason": "人工取消确认"}
    assert client.post(url, json=payload).status_code == 400
    payload["confirmation"] = f"  {recovery_alert.id}  "
    result = client.post(url, json=payload)
    assert result.status_code == 200 and result.json()["status"] == "cancelled"
    assert result.json()["last_evaluation"]["operator"] == f"recovery-{role}"


def test_rejected_target_change_does_not_save_inline_credential(
    client: TestClient, database_session: Session, recovery_alert: Alert,
) -> None:
    login(client, database_session)
    start(client, recovery_alert)
    before = list(database_session.execute(select(
        Credential.id, Credential.encrypted_secret, Credential.nonce,
    )))
    response = client.patch(f"/api/v1/assets/{recovery_alert.asset_id}", json={
        "host": "192.0.2.11", "ssh_username": "demo-reader",
        "ssh_credential_type": "password", "ssh_secret": "DEMO-only-password1!",
    })
    assert response.status_code == 409
    assert list(database_session.execute(select(
        Credential.id, Credential.encrypted_secret, Credential.nonce,
    ))) == before
    assert database_session.scalar(select(func.count()).select_from(Credential)) == len(before)
