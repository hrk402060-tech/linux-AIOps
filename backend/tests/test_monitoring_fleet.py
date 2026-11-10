from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from src.database import get_session
from src.models.asset import (
    Asset, AssetEnvironment, AssetLifecycleStatus, OsFamily,
)
from src.models.alert import Alert, AlertConnector
from src.models.monitoring import MonitoringAlertState, MonitoringPolicy, MonitoringSnapshot
from test_user_asset_scope_monitoring import _grant, _sign_in, _user


def make_asset(session: Session, name: str, **values: object) -> Asset:
    asset = Asset(
        id=str(uuid4()), name=name, host="10.0.0.21", os_family=OsFamily.UBUNTU,
        environment=AssetEnvironment.TEST, group_name="test-fleet", **values,
    )
    session.add(asset)
    session.flush()
    return asset


def make_snapshot(
    session: Session, asset: Asset, when: datetime, status: str = "online",
    **metrics: object,
) -> MonitoringSnapshot:
    snapshot = MonitoringSnapshot(
        id=str(uuid4()), asset_id=asset.id, status=status,
        metrics=metrics, collected_at=when, read_only=True,
    )
    session.add(snapshot)
    return snapshot


def make_alert(
    session: Session, asset: Asset, status: str, source_type: str = "external",
) -> None:
    identifier = str(uuid4())
    connector = AlertConnector(
        id=identifier, name=identifier, source_type="generic",
        encrypted_secret=b"test-cipher", nonce=b"0" * 12,
        key_version="test-v1", secret_hint="test", operator="test",
    )
    session.add(connector)
    session.flush()
    session.add(Alert(
        id=str(uuid4()), connector_id=connector.id, external_id=identifier,
        title="Fleet alert", description="Test alert", severity="high",
        status=status, asset_id=asset.id, asset_name=asset.name,
        sanitized_payload={}, source_type=source_type,
    ))


def test_fleet_get_is_complete_and_never_writes(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    for index in range(105):
        make_asset(database_session, f"fleet-{index:03}")
    archived = make_asset(
        database_session, "fleet-archived",
        lifecycle_status=AssetLifecycleStatus.ARCHIVED,
    )
    database_session.commit()
    writes: list[str] = []
    reads: list[str] = []

    def capture(
        _connection: object, _cursor: object, statement: str, _parameters: object,
        _context: object, _executemany: bool,
    ) -> None:
        operation = statement.lstrip().split(" ", 1)[0].upper()
        if operation in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)
        elif operation == "SELECT":
            reads.append(statement)

    engine = database_session.get_bind()
    with engine.connect() as connection:
        def request_session() -> Generator[Session, None, None]:
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as session:
                yield session

        previous_override = client.app.dependency_overrides.get(get_session)
        client.app.dependency_overrides[get_session] = request_session
        event.listen(connection, "before_cursor_execute", capture)
        try:
            response = client.get("/api/v1/monitoring/fleet")
        finally:
            event.remove(connection, "before_cursor_execute", capture)
            if previous_override is None:
                client.app.dependency_overrides.pop(get_session)
            else:
                client.app.dependency_overrides[get_session] = previous_override
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["assets"]) == 105
    assert archived.id not in response.text
    assert body["stale_after_seconds"] == 900
    assert datetime.fromisoformat(body["generated_at"]) >= now
    row = body["assets"][0]
    assert row["group_name"] == "test-fleet"
    assert row["latest_snapshot"] is None
    assert row["policy"]["updated_at"] is None
    assert row["policy"]["severity_rules"]["cpu"] == {"warning": 70, "critical": 90}
    assert set(row["resource_statuses"].values()) == {"unknown"}
    assert reads
    assert not writes
    assert database_session.scalar(select(func.count()).select_from(MonitoringPolicy)) == 0


def test_fleet_uses_real_alert_counts_and_continuous_resource_history(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    asset = make_asset(database_session, "fleet-resources")
    for minutes in (8, 4, 0):
        make_snapshot(
            database_session, asset, now - timedelta(minutes=minutes),
            cpu_percent=96, memory_percent=75, disk_percent=30,
            load_1m=5, cpu_count=4,
        )
    database_session.add(MonitoringAlertState(
        id=str(uuid4()), asset_id=asset.id, metric="availability",
        status="triggered", last_value=0, threshold=1, triggered_at=now,
    ))
    make_alert(database_session, asset, "open", "monitoring")
    database_session.commit()
    response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    row = response.json()["assets"][0]
    assert row["active_alerts"] == 1
    assert row["resource_statuses"] == {
        "cpu": "critical", "memory": "warning", "disk": "normal", "load": "critical",
    }
    assert database_session.scalar(select(func.count()).select_from(MonitoringAlertState)) == 1


def test_fleet_counts_shared_active_alerts_without_counting_bridge_twice(
    client: TestClient, database_session: Session,
) -> None:
    asset = make_asset(database_session, "shared-alerts")
    make_alert(database_session, asset, "open", "monitoring")
    for status in ("open", "acknowledged", "in_progress", "resolved", "closed"):
        make_alert(database_session, asset, status)
    database_session.add(MonitoringAlertState(
        id=str(uuid4()), asset_id=asset.id, metric="cpu_percent",
        status="triggered", last_value=99, threshold=85,
    ))
    database_session.commit()
    response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    assert response.json()["assets"][0]["active_alerts"] == 4


def test_fleet_missing_offline_stale_and_disabled_are_unknown(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    missing = make_asset(database_session, "missing")
    make_snapshot(database_session, missing, now, memory_percent=0)
    for name, status, minutes, lifecycle in (
        ("offline", "offline", 0, AssetLifecycleStatus.ENABLED),
        ("stale", "online", 16, AssetLifecycleStatus.ENABLED),
        ("future", "online", -1, AssetLifecycleStatus.ENABLED),
        ("disabled", "online", 0, AssetLifecycleStatus.DISABLED),
    ):
        asset = make_asset(database_session, name, lifecycle_status=lifecycle)
        make_snapshot(
            database_session, asset, now - timedelta(minutes=minutes), status,
            cpu_percent=99, memory_percent=99, disk_percent=99,
            load_1m=99, cpu_count=4,
        )
    database_session.commit()
    rows = {row["asset_name"]: row for row in client.get(
        "/api/v1/monitoring/fleet"
    ).json()["assets"]}
    assert rows["missing"]["resource_statuses"] == {
        "cpu": "unknown", "memory": "normal", "disk": "unknown", "load": "unknown",
    }
    for name in ("offline", "stale", "future", "disabled"):
        assert set(rows[name]["resource_statuses"].values()) == {"unknown"}
    assert rows["stale"]["stale"] is True
    assert rows["future"]["stale"] is True


def test_fleet_and_trends_enforce_asset_scope(
    client: TestClient, database_session: Session,
) -> None:
    allowed = make_asset(database_session, "fleet-allowed")
    hidden = make_asset(database_session, "fleet-hidden")
    database_session.commit()
    user = _user(client)
    _grant(client, user, allowed.id)
    _sign_in(client, user)
    response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    assert [row["asset_id"] for row in response.json()["assets"]] == [allowed.id]
    assert hidden.id not in response.text
    denied = client.get(
        "/api/v1/monitoring/fleet/trends",
        params=[("asset_ids", allowed.id), ("asset_ids", hidden.id)],
    )
    assert denied.status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/monitoring/fleet").status_code == 401


def test_auditor_can_read_authorized_fleet_but_cannot_change_rules(
    client: TestClient, database_session: Session,
) -> None:
    allowed = make_asset(database_session, "audit-visible")
    hidden = make_asset(database_session, "audit-hidden")
    make_snapshot(database_session, allowed, datetime.now(timezone.utc), cpu_percent=25)
    database_session.commit()
    response = client.post("/api/v1/auth/users", json={
        "username": "monitor-auditor", "password": "Scope-Test-2026!",
        "role": "auditor",
    })
    assert response.status_code == 201, response.text
    auditor = response.json()
    _grant(client, auditor, allowed.id)
    _sign_in(client, auditor)
    fleet = client.get("/api/v1/monitoring/fleet")
    assert fleet.status_code == 200, fleet.text
    assert [row["asset_id"] for row in fleet.json()["assets"]] == [allowed.id]
    assert client.get(
        "/api/v1/monitoring/fleet/trends", params={"asset_ids": allowed.id},
    ).status_code == 200
    assert client.get(
        "/api/v1/monitoring/fleet/trends", params={"asset_ids": hidden.id},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/monitoring/assets/{allowed.id}/policy",
        json={"severity_rules": {"duration_minutes": 0}},
    ).status_code == 403
    assert database_session.get(MonitoringPolicy, allowed.id) is None
