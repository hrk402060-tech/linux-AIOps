from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.monitoring import MonitoringEvent, MonitoringSnapshot


ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _asset(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/assets", headers=ADMIN, json={
        "name": name,
        "host": "10.0.0.41",
        "ssh_username": "ops",
        "ssh_secret": "monitor-secret",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _user(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/users", headers=ADMIN, json={
        "username": "monitor-scoped-user",
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
            "reason": "监控范围隔离测试",
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


def test_scoped_user_only_sees_authorized_monitoring_data(
    client: TestClient, database_session: Session
) -> None:
    user = _user(client)
    allowed_asset = _asset(client, "monitor-allowed")
    hidden_asset = _asset(client, "monitor-hidden")
    now = datetime.now(timezone.utc)
    database_session.add_all([
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=allowed_asset, status="online",
            read_only=True, metrics={"cpu_percent": 10}, collected_at=now,
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=hidden_asset, status="offline",
            read_only=True, metrics={}, error_message="hidden", collected_at=now,
        ),
        MonitoringEvent(
            id=str(uuid4()), asset_id=allowed_asset, asset_name="monitor-allowed",
            metric="cpu_percent", event_type="triggered", value=91, threshold=85,
            message="allowed", occurred_at=now,
        ),
        MonitoringEvent(
            id=str(uuid4()), asset_id=hidden_asset, asset_name="monitor-hidden",
            metric="cpu_percent", event_type="triggered", value=99, threshold=85,
            message="hidden", occurred_at=now,
        ),
    ])
    database_session.commit()
    _grant(client, user, allowed_asset)
    _sign_in(client, user)

    overview = client.get("/api/v1/monitoring/overview")
    assert overview.status_code == 200
    assert overview.json()["summary"]["total"] == 1
    assert [item["asset_id"] for item in overview.json()["assets"]] == [allowed_asset]
    events = client.get("/api/v1/monitoring/events")
    assert events.status_code == 200
    assert [item["asset_id"] for item in events.json()] == [allowed_asset]

    assert client.get(
        f"/api/v1/monitoring/assets/{hidden_asset}/snapshots"
    ).status_code == 403
    assert client.post(
        f"/api/v1/monitoring/assets/{hidden_asset}/collect"
    ).status_code == 403
    assert client.patch(
        f"/api/v1/monitoring/assets/{hidden_asset}/policy",
        json={"cpu_threshold": 80},
    ).status_code == 403
