from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from probe_scope_support import seed_target, sign_in
from src.models.alert import Alert, AlertConnector
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.cmdb import CmdbServiceApi, CmdbServiceAsset, ServiceAssetStatus
from src.models.monitoring import MonitoringAlertState, MonitoringSnapshot
from src.models.probes import HttpProbe, HttpProbeRun


def seed(session: Session) -> dict[str, str]:
    target = seed_target(session, "orders", "192.0.2.80")
    session.add(MonitoringSnapshot(
        id=str(uuid4()), asset_id=target["asset"], status="online",
        metrics={"cpu_percent": 25}, collected_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return target


def add_probe(
    session: Session, target: dict[str, str], *, status: str = "healthy",
    age: int = 0, probe_status: str = "active", run_id: str | None = None,
) -> tuple[HttpProbe, HttpProbeRun]:
    probe = HttpProbe(
        id=str(uuid4()), name="Order health", service_id=target["service"],
        asset_id=target["asset"], api_id=target["api"],
        url="http://192.0.2.80/health", method="GET", expected_status=200,
        timeout_seconds=3, status=probe_status, operator_username="test",
    )
    run = HttpProbeRun(
        id=run_id or str(uuid4()), probe_id=probe.id, status=status,
        business_id=target["business"], application_id=target["application"],
        service_id=target["service"], asset_id=target["asset"], api_id=target["api"],
        http_status=200 if status == "healthy" else None, latency_ms=5,
        error_code=None if status == "healthy" else "timeout",
        config_snapshot={key: getattr(probe, key) for key in (
            "url", "method", "expected_status", "timeout_seconds",
        )},
        operator_username="test", trace_id=str(uuid4()),
        checked_at=datetime.now(timezone.utc) - timedelta(seconds=age),
    )
    session.add_all([probe, run])
    session.commit()
    return probe, run


def read(client: TestClient) -> dict[str, object]:
    response = client.get("/api/v1/observability/services")
    assert response.status_code == 200, response.text
    return response.json()["services"][0]


def test_online_host_is_not_business_health(client: TestClient, database_session: Session) -> None:
    seed(database_session)
    row = read(client)
    assert row["runtime_status"] == "healthy"
    assert row["status"] == "unobserved"
    assert "probes_unconfigured" in {item["code"] for item in row["health_reasons"]}


@pytest.mark.parametrize("run_status,age,probe_status,health,evidence", [
    ("healthy", 0, "active", "healthy", "healthy"),
    ("unhealthy", 0, "active", "degraded", "failed"),
    ("healthy", 3600, "active", "unobserved", "stale"),
    ("unhealthy", 3600, "active", "unobserved", "stale"),
    ("healthy", -600, "active", "unobserved", "stale"),
    ("healthy", 0, "disabled", "unobserved", "disabled"),
])
def test_probe_evidence_controls_health(
    client: TestClient, database_session: Session,
    run_status: str, age: int, probe_status: str, health: str, evidence: str,
) -> None:
    target = seed(database_session)
    add_probe(database_session, target, status=run_status, age=age, probe_status=probe_status)
    row = read(client)
    assert row["status"] == health
    assert row["probes"][0]["status"] == evidence
    assert "url" not in row["probes"][0]


@pytest.mark.parametrize("mutation", ["url", "method", "expected_status", "timeout_seconds", "context", "no_run", "api_path"])
def test_obsolete_probe_evidence_is_not_reused(
    client: TestClient, database_session: Session, mutation: str,
) -> None:
    target = seed(database_session)
    probe, run = add_probe(database_session, target)
    if mutation == "context":
        run.application_id = str(uuid4())
    elif mutation == "no_run":
        database_session.delete(run)
    elif mutation == "api_path":
        database_session.get(CmdbServiceApi, target["api"]).path = "/changed"
    else:
        setattr(probe, mutation, {"url": "http://192.0.2.80/changed", "method": "HEAD",
                                "expected_status": 204, "timeout_seconds": 9}[mutation])
    database_session.commit()
    row = read(client)
    assert row["status"] == "unobserved"
    assert row["probes"][0]["status"] == "unobserved"
    assert row["probes"][0]["latency_ms"] is None


def test_latest_run_uses_id_tie_break_and_missing_api_coverage(
    client: TestClient, database_session: Session,
) -> None:
    target = seed(database_session)
    probe, run = add_probe(database_session, target, run_id="a")
    newer = HttpProbeRun(**{column.name: getattr(run, column.name) for column in run.__table__.columns})
    newer.id, newer.status, newer.error_code = "z", "unhealthy", "timeout"
    database_session.add(newer)
    database_session.commit()
    assert read(client)["status"] == "degraded"
    database_session.delete(newer)
    database_session.add(CmdbServiceApi(
        id=str(uuid4()), service_id=target["service"], code="other",
        name="Other API", path="/other", http_method="GET",
    ))
    database_session.commit()
    row = read(client)
    assert row["status"] == "unobserved"
    assert row["probe_summary"]["api_count"] == 2
    assert row["probe_summary"]["covered_api_count"] == 1


@pytest.mark.parametrize("state,age,lifecycle,expected", [
    ("offline", 0, AssetLifecycleStatus.ENABLED, "unavailable"),
    ("offline", 3600, AssetLifecycleStatus.ENABLED, "unobserved"),
    ("online", -600, AssetLifecycleStatus.ENABLED, "unobserved"),
    ("online", 0, AssetLifecycleStatus.DISABLED, "unobserved"),
])
def test_invalid_runtime_metrics_do_not_become_current_evidence(
    client: TestClient, database_session: Session,
    state: str, age: int, lifecycle: AssetLifecycleStatus, expected: str,
) -> None:
    target = seed(database_session)
    asset = database_session.get(Asset, target["asset"])
    asset.lifecycle_status = lifecycle
    snapshot = database_session.scalar(select(MonitoringSnapshot))
    snapshot.status, snapshot.metrics = state, {"cpu_percent": 99}
    snapshot.collected_at = datetime.now(timezone.utc) - timedelta(seconds=age)
    database_session.commit()
    row = read(client)
    assert row["runtime_status"] == expected
    assert row["resource_peaks"]["cpu_percent"] is None
    assert row["status"] != "healthy"


def test_alert_bridge_is_counted_once_and_response_is_read_only(
    client: TestClient, database_session: Session,
) -> None:
    target = seed(database_session)
    add_probe(database_session, target)
    connector = AlertConnector(
        id=str(uuid4()), name="test connector", source_type="prometheus",
        encrypted_secret=b"x", nonce=b"x", key_version="test", secret_hint="x",
        operator="test",
    )
    database_session.add(connector)
    database_session.flush()
    for index, source in enumerate(("platform_monitoring", "prometheus")):
        database_session.add(Alert(
            id=str(uuid4()), connector_id=connector.id, external_id=str(index),
            title="password=private", description="secret", severity="high",
            status="open", asset_id=target["asset"], asset_name="orders",
            sanitized_payload={"token": "private"}, source_type=source,
            source_key=f"{target['asset']}:cpu_percent" if index == 0 else None,
        ))
    database_session.add(MonitoringAlertState(
        id=str(uuid4()), asset_id=target["asset"], metric="cpu_percent",
        status="triggered", last_value=99, threshold=85,
    ))
    database_session.commit()
    before = database_session.scalar(select(func.count()).select_from(HttpProbeRun))
    with patch("src.services.probes.check_http") as network:
        row = read(client)
    network.assert_not_called()
    assert row["active_alerts"] == 2
    assert row["status"] == "degraded"
    assert "private" not in str(row)
    assert database_session.scalar(select(func.count()).select_from(HttpProbeRun)) == before


def test_scoped_evidence_cannot_leak_or_claim_full_health(
    client: TestClient, database_session: Session,
) -> None:
    target = seed(database_session)
    add_probe(database_session, target)
    hidden = seed_target(database_session, "secret", "192.0.2.99")
    database_session.add(CmdbServiceAsset(
        id=str(uuid4()), asset_id=hidden["asset"], service_id=target["service"],
    ))
    database_session.commit()
    sign_in(client, database_session, [target["asset"]])
    row = read(client)
    assert row["health_scope"] == "authorized"
    assert not row["scope_complete"]
    assert row["status"] == "unobserved"
    assert hidden["asset"] not in str(row)
    assert "192.0.2.99" not in str(row)
    assert row["asset_count"] == 1
    relation = database_session.scalar(select(CmdbServiceAsset).where(
        CmdbServiceAsset.asset_id == target["asset"],
    ))
    relation.status = ServiceAssetStatus.UNLINKED
    database_session.commit()
    assert client.get("/api/v1/observability/services").json()["services"] == []
