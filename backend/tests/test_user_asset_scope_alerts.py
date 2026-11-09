from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertTimeline


ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _asset(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/assets", headers=ADMIN, json={
        "name": name,
        "host": "10.0.0.51",
        "ssh_username": "ops",
        "ssh_secret": "alert-secret",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _user(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/users", headers=ADMIN, json={
        "username": "alert-scoped-user",
        "password": "Scope-Test-2026!",
        "role": "operator",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _grant(client: TestClient, user: dict[str, str], asset_id: str) -> None:
    revision = client.get(
        f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN
    ).json()["revision"]
    response = client.put(
        f"/api/v1/auth/users/{user['id']}/asset-access",
        headers=ADMIN,
        json={
            "asset_scope": "assigned",
            "expected_revision": revision,
            "entries": [{"asset_id": asset_id, "can_ssh": False}],
            "confirmation": user["username"],
            "reason": "告警范围隔离测试",
        },
    )
    assert response.status_code == 200, response.text


def _sign_in(client: TestClient, user: dict[str, str]) -> None:
    client.headers.pop("authorization", None)
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={
        "username": user["username"],
        "password": "Scope-Test-2026!",
    })
    assert response.status_code == 200, response.text


def _alert(
    session: Session, connector_id: str, asset_id: str, suffix: str
) -> str:
    alert_id = f"alert-{suffix}"
    now = datetime.now(timezone.utc)
    session.add(Alert(
        id=alert_id,
        connector_id=connector_id,
        external_id=f"external-{suffix}",
        title=f"告警 {suffix}",
        description="范围隔离告警",
        severity="high",
        status="open",
        asset_id=asset_id,
        asset_name=f"alert-{suffix}",
        sanitized_payload={"source": "test"},
        received_at=now,
        updated_at=now,
    ))
    session.add(AlertTimeline(
        alert_id=alert_id,
        connector_id=connector_id,
        event_type="ingested",
        status="open",
        note="test",
        operator="test",
        trace_id=str(uuid4()),
        details={},
        created_at=now,
    ))
    session.commit()
    return alert_id


def test_scoped_user_cannot_read_or_change_hidden_alert(
    client: TestClient, database_session: Session
) -> None:
    connector = client.post("/api/v1/alert-connectors", headers=ADMIN, json={
        "name": "Scope alert connector",
        "source_type": "prometheus",
    }).json()
    user = _user(client)
    allowed_asset = _asset(client, "alert-allowed")
    hidden_asset = _asset(client, "alert-hidden")
    allowed_alert = _alert(database_session, connector["id"], allowed_asset, "allowed")
    hidden_alert = _alert(database_session, connector["id"], hidden_asset, "hidden")
    _grant(client, user, allowed_asset)
    _sign_in(client, user)

    listing = client.get("/api/v1/alerts")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert [item["id"] for item in listing.json()["items"]] == [allowed_alert]
    assert client.get(
        f"/api/v1/alerts?asset_id={hidden_asset}"
    ).json()["total"] == 0
    assert client.get(f"/api/v1/alerts/{allowed_alert}").status_code == 200

    for path, method, payload in (
        (f"/api/v1/alerts/{hidden_alert}", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/timeline", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/analyses", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/diagnostic-jobs", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/recovery-verifications", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/notifications", "get", None),
        (f"/api/v1/alerts/{hidden_alert}/diagnostics", "post", {
            "confirmation": hidden_alert, "reason": "越权测试",
        }),
        (f"/api/v1/alerts/{hidden_alert}/recovery-verifications", "post", {
            "confirmation": hidden_alert, "reason": "越权测试",
            "kind": "monitor", "metric": "cpu_percent", "threshold": 90,
            "observation_minutes": 1, "max_gap_seconds": 30,
        }),
        (f"/api/v1/alerts/{hidden_alert}/status", "patch", {
            "status": "acknowledged", "note": "越权测试",
        }),
        (f"/api/v1/alerts/{hidden_alert}/incident", "post", {
            "description": "越权测试",
        }),
    ):
        response = getattr(client, method)(path, json=payload) if payload else getattr(
            client, method
        )(path)
        assert response.status_code == 403
