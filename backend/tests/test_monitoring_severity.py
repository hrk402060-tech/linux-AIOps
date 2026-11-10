from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from test_monitoring_fleet import make_asset, make_snapshot


def _rules(duration: int = 5) -> dict[str, object]:
    return {
        "cpu": {"warning": 70, "critical": 90},
        "memory": {"warning": 70, "critical": 90},
        "disk": {"warning": 70, "critical": 90},
        "load": {"warning": 0.7, "critical": 1.0},
        "duration_minutes": duration,
    }


@pytest.mark.parametrize("interruption", ["offline", "missing", "gap", "normal"])
def test_severity_requires_continuous_observed_breach(
    client: TestClient, database_session: Session, interruption: str,
) -> None:
    now = datetime.now(timezone.utc)
    asset = make_asset(database_session, "duration-" + interruption)
    make_snapshot(
        database_session, asset, now - timedelta(minutes=30), cpu_percent=99,
    )
    if interruption != "gap":
        make_snapshot(
            database_session, asset, now - timedelta(minutes=2),
            "offline" if interruption == "offline" else "online",
            **({} if interruption == "missing" else {"cpu_percent": 10}),
        )
    make_snapshot(database_session, asset, now, cpu_percent=99)
    database_session.commit()
    with patch("src.services.monitoring_fleet.utc_now", return_value=now):
        response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    assert response.json()["assets"][0]["resource_statuses"]["cpu"] == "pending_critical"


def test_load_is_normalized_by_remote_logical_cpus_and_unknown_without_them(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    for name, cores in (("eight-cores", 8), ("one-core", 1), ("unknown-cores", None)):
        asset = make_asset(database_session, name)
        for minutes in (6, 0):
            make_snapshot(
                database_session, asset, now - timedelta(minutes=minutes),
                cpu_count=cores, load_1m=4,
            )
    database_session.commit()
    rows = client.get("/api/v1/monitoring/fleet").json()["assets"]
    statuses = {row["asset_name"]: row["resource_statuses"]["load"] for row in rows}
    assert statuses == {
        "eight-cores": "normal", "one-core": "critical", "unknown-cores": "unknown",
    }


def test_established_warning_remains_until_critical_duration_is_observed(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    asset = make_asset(database_session, "warning-before-critical")
    make_snapshot(
        database_session, asset, now - timedelta(minutes=6), cpu_percent=80,
    )
    make_snapshot(database_session, asset, now, cpu_percent=95)
    database_session.commit()
    with patch("src.services.monitoring_fleet.utc_now", return_value=now):
        response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    assert response.json()["assets"][0]["resource_statuses"]["cpu"] == "warning"


@pytest.mark.parametrize("value", [None, True, "98", -1, 101, 10 ** 400])
def test_invalid_resource_values_remain_unknown(
    client: TestClient, database_session: Session, value: object,
) -> None:
    asset = make_asset(database_session, "invalid-observation")
    make_snapshot(
        database_session, asset, datetime.now(timezone.utc),
        cpu_percent=value, memory_percent=0,
    )
    database_session.commit()
    response = client.get("/api/v1/monitoring/fleet")
    assert response.status_code == 200, response.text
    statuses = response.json()["assets"][0]["resource_statuses"]
    assert statuses["cpu"] == "unknown"
    assert statuses["memory"] == "normal"


def test_severity_rules_are_persisted_separately_and_can_reset_to_defaults(
    client: TestClient, database_session: Session,
) -> None:
    asset = make_asset(database_session, "configured-rules")
    database_session.commit()
    rules = _rules(0)
    rules["cpu"] = {"warning": 40, "critical": 60}
    response = client.patch(
        f"/api/v1/monitoring/assets/{asset.id}/policy", json={"severity_rules": rules},
    )
    assert response.status_code == 200, response.text
    assert response.json()["severity_rules"] == rules
    assert response.json()["cpu_threshold"] == 85
    make_snapshot(database_session, asset, datetime.now(timezone.utc), cpu_percent=65)
    database_session.commit()
    row = client.get("/api/v1/monitoring/fleet").json()["assets"][0]
    assert row["resource_statuses"]["cpu"] == "critical"
    assert row["active_alerts"] == 0
    assert client.get("/api/v1/monitoring/events").json() == []
    reset = client.patch(
        f"/api/v1/monitoring/assets/{asset.id}/policy",
        json={"severity_rules": None},
    )
    assert reset.status_code == 200
    assert reset.json()["severity_rules"] == _rules()


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu", {"warning": 90, "critical": 70}),
        ("cpu", {"warning": 70, "critical": 70}),
        ("memory", {"warning": -1, "critical": 90}),
        ("disk", {"warning": 70, "critical": 101}),
        ("load", {"warning": 0, "critical": 1}),
        ("load", {"warning": 1, "critical": 1001}),
        ("duration_minutes", -1),
        ("duration_minutes", 1441),
        ("duration_minutes", True),
        ("duration_minutes", 1.5),
        ("cpu", {"warning": True, "critical": 90}),
        ("cpu", {"warning": 70, "critical": 90, "extra": 1}),
        ("load", {"warning": "0.7", "critical": 1}),
        ("disk", None),
        ("unexpected", {"warning": 1, "critical": 2}),
    ],
)
def test_invalid_severity_rules_do_not_persist(
    client: TestClient, database_session: Session, field: str, value: object,
) -> None:
    asset = make_asset(database_session, "invalid-rules")
    database_session.commit()
    rules = {**_rules(), field: value}
    response = client.patch(
        f"/api/v1/monitoring/assets/{asset.id}/policy", json={"severity_rules": rules},
    )
    assert response.status_code == 422, response.text
    row = client.get("/api/v1/monitoring/fleet").json()["assets"][0]
    assert row["policy"]["severity_rules"] == _rules()
