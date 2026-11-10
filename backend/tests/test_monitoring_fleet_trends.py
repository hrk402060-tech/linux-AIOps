from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from src.models.asset import AssetLifecycleStatus
from src.models.monitoring import MonitoringPolicy, MonitoringSnapshot
from test_monitoring_fleet import make_asset, make_snapshot


def test_trends_deduplicate_assets_and_use_independent_valid_denominators(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc)
    first = make_asset(database_session, "trend-first")
    second = make_asset(database_session, "trend-second")
    for asset, cpu, memory in ((first, 10, 20), (second, 90, None)):
        make_snapshot(
            database_session, asset, now - timedelta(minutes=5),
            cpu_percent=cpu, memory_percent=memory,
        )
    make_snapshot(
        database_session, first, now - timedelta(minutes=4),
        cpu_percent=30, memory_percent=40,
    )
    database_session.commit()
    with patch("src.services.monitoring_fleet.utc_now", return_value=now):
        response = client.get(
            "/api/v1/monitoring/fleet/trends",
            params=[("asset_ids", first.id), ("asset_ids", first.id),
                    ("asset_ids", second.id), ("hours", 24)],
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["buckets"]) == 24
    assert body["bucket_seconds"] == 3600
    point = body["buckets"][-1]
    assert point["cpu_percent"] == 60
    assert point["memory_percent"] == 40
    assert point["cpu_asset_count"] == 2
    assert point["memory_asset_count"] == 1
    assert body["buckets"][0]["cpu_percent"] is None
    assert body["buckets"][0]["cpu_asset_count"] == 0


def test_trends_exclude_latest_offline_stale_disabled_and_absent_values(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    ids = []
    for name in ("offline", "stale", "disabled", "missing", "healthy"):
        asset = make_asset(
            database_session, f"trend-{name}",
            lifecycle_status=AssetLifecycleStatus.DISABLED
            if name == "disabled" else AssetLifecycleStatus.ENABLED,
        )
        ids.append(asset.id)
        make_snapshot(
            database_session, asset, now - timedelta(minutes=20),
            cpu_percent=99, memory_percent=99,
        )
        if name != "stale":
            make_snapshot(
                database_session, asset, now - timedelta(minutes=1),
                "offline" if name == "offline" else "online",
                **({"cpu_percent": 10, "memory_percent": 20} if name != "missing" else {}),
            )
    database_session.commit()
    response = client.get(
        "/api/v1/monitoring/fleet/trends",
        params=[("asset_ids", asset_id) for asset_id in ids],
    )
    assert response.status_code == 200, response.text
    points = response.json()["buckets"]
    populated = [point for point in points if point["cpu_percent"] is not None]
    assert populated
    assert all(point["cpu_percent"] == 10 for point in populated)
    assert all(point["cpu_asset_count"] == 1 for point in populated)


def test_trends_never_fall_back_to_all_assets_and_reject_unknown_ids(
    client: TestClient, database_session: Session,
) -> None:
    asset = make_asset(database_session, "trend-only")
    make_snapshot(database_session, asset, datetime.now(timezone.utc), cpu_percent=55)
    archived = make_asset(
        database_session, "trend-archived",
        lifecycle_status=AssetLifecycleStatus.ARCHIVED,
    )
    database_session.commit()
    assert client.get("/api/v1/monitoring/fleet/trends").json()["buckets"] == []
    assert client.get(
        "/api/v1/monitoring/fleet/trends", params={"asset_ids": ""},
    ).json()["buckets"] == []
    for asset_id in ("missing-id", archived.id):
        response = client.get(
            "/api/v1/monitoring/fleet/trends", params={"asset_ids": asset_id},
        )
        assert response.status_code == 404
    assert client.get(
        "/api/v1/monitoring/fleet/trends", params={"asset_ids": asset.id, "hours": 0},
    ).status_code == 422


def test_maximum_range_bounds_loaded_snapshots_by_asset_and_bucket(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc)
    asset = make_asset(database_session, "bounded-history")
    for index in range(600):
        make_snapshot(
            database_session, asset,
            now - timedelta(days=89, minutes=20) + timedelta(seconds=index),
            cpu_percent=20,
        )
    make_snapshot(database_session, asset, now, cpu_percent=75)
    database_session.commit()
    loaded: list[object] = []

    def capture(snapshot: object, _context: object) -> None:
        loaded.append(snapshot)

    event.listen(MonitoringSnapshot, "load", capture)
    try:
        with patch("src.services.monitoring_fleet.utc_now", return_value=now):
            response = client.get(
                "/api/v1/monitoring/fleet/trends",
                params={"asset_ids": asset.id, "hours": 2160},
            )
    finally:
        event.remove(MonitoringSnapshot, "load", capture)
    assert response.status_code == 200, response.text
    points = response.json()["buckets"]
    assert len(points) == 24
    assert points[0]["cpu_percent"] == 20
    assert points[-1]["cpu_percent"] == 75
    assert points[1]["cpu_percent"] is None
    assert len(loaded) <= 3


def test_bucket_uses_last_observation_even_when_it_is_offline(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime(2026, 11, 10, 12, 30, tzinfo=timezone.utc)
    asset = make_asset(database_session, "bucket-offline")
    make_snapshot(
        database_session, asset, now - timedelta(hours=2, minutes=10),
        cpu_percent=80,
    )
    make_snapshot(
        database_session, asset, now - timedelta(hours=2, minutes=5), "offline",
    )
    make_snapshot(database_session, asset, now, cpu_percent=30)
    database_session.commit()
    with patch("src.services.monitoring_fleet.utc_now", return_value=now):
        response = client.get(
            "/api/v1/monitoring/fleet/trends",
            params={"asset_ids": asset.id, "hours": 24},
        )
    assert response.status_code == 200, response.text
    points = response.json()["buckets"]
    assert points[-3]["cpu_percent"] is None
    assert points[-3]["cpu_asset_count"] == 0
    assert points[-1]["cpu_percent"] == 30


def test_trends_get_never_writes_or_creates_monitoring_policy(
    client: TestClient, database_session: Session,
) -> None:
    asset = make_asset(database_session, "trend-read-only")
    make_snapshot(database_session, asset, datetime.now(timezone.utc), cpu_percent=25)
    database_session.commit()
    writes: list[str] = []

    def capture(
        _connection: object, _cursor: object, statement: str, _parameters: object,
        _context: object, _executemany: bool,
    ) -> None:
        if statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    engine = database_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(
            "/api/v1/monitoring/fleet/trends", params={"asset_ids": asset.id},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert not writes
    assert database_session.get(MonitoringPolicy, asset.id) is None
