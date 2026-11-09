from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.models.asset import AssetLifecycleStatus
from src.models.auth import User
from src.models.cmdb import CmdbServiceAsset, ServiceAssetStatus
from src.models.monitoring import MonitoringAlertState, MonitoringSnapshot
from src.models.scan_task import ScanStatus
from test_asset_scope_scan_cmdb import _create_service
from test_dashboard_api import _finding, _task
from test_user_asset_scope_alerts import ADMIN, _asset, _grant, _sign_in, _user


def _context(client: TestClient, session: Session) -> tuple[dict[str, str], str, str]:
    allowed = _asset(client, "dashboard-allowed")
    hidden = _asset(client, "dashboard-hidden")
    user = _user(client)
    _grant(client, user, allowed)
    service_id = _create_service(client, "SHARED")
    session.add_all([
        CmdbServiceAsset(id=str(uuid4()), service_id=service_id, asset_id=asset_id)
        for asset_id in (allowed, hidden)
    ])
    now = datetime.now(timezone.utc)
    for asset_id, cpu in ((allowed, 42), (hidden, 99)):
        session.add(MonitoringSnapshot(
            id=str(uuid4()), asset_id=asset_id, status="online", read_only=True,
            metrics={"cpu_percent": cpu}, collected_at=now,
        ))
        if asset_id == hidden:
            session.add(MonitoringAlertState(
                id=str(uuid4()), asset_id=asset_id, metric="cpu_percent",
                status="triggered", last_value=cpu, threshold=85, triggered_at=now,
            ))
    session.commit()
    return user, allowed, hidden


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_dashboard_and_shared_service_only_aggregate_visible_assets(
    client: TestClient, database_session: Session, role: str,
) -> None:
    user, allowed, hidden = _context(client, database_session)
    account = database_session.get(User, user["id"])
    account.role = role
    database_session.commit()
    _sign_in(client, user)

    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assets"]["total"] == 1
    assert body["catalog"]["linked_assets"] == 1
    assert [item["asset_id"] for item in body["monitoring_assets"]] == [allowed]
    assert body["resource_trends"][0]["cpu_percent"] == 42
    assert body["operations"]["active_alerts"] == 0
    assert body["active_alerts"] == []
    assert body["resource_alerts"] == {"cpu": 0, "memory": 0, "disk": 0}
    service = body["service_health"]["services"][0]
    assert service["asset_count"] == 1
    assert service["resource_peaks"]["cpu_percent"] == 42
    assert service["status"] == "healthy"
    assert hidden not in response.text
    observed = client.get("/api/v1/observability/services")
    assert observed.status_code == 200, observed.text
    assert observed.json()["services"][0]["asset_count"] == 1
    assert hidden not in observed.text


def test_empty_asset_scope_has_no_operational_statistics_or_search_results(
    client: TestClient, database_session: Session,
) -> None:
    user, _, hidden = _context(client, database_session)
    access = client.get(
        f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN,
    ).json()
    assert client.put(
        f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN,
        json={"asset_scope": "assigned", "expected_revision": access["revision"],
              "entries": [], "confirmation": user["username"], "reason": "remove access"},
    ).status_code == 200
    _sign_in(client, user)
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()
    for section in ("assets", "vulnerabilities", "scans", "catalog", "operations"):
        assert not any(body[section].values()), section
    for section in ("monitoring_assets", "resource_trends", "active_alerts",
                    "recent_scans", "affected_assets", "attention_items"):
        assert body[section] == [], section
    assert body["service_health"]["summary"]["total"] == 0
    assert body["knowledge"]["source_count"] == 9
    assert client.get("/api/v1/assets").json() == []
    assert client.get("/api/v1/cmdb/tree?query=SHARED").json()["businesses"] == []
    assert client.get("/api/v1/vulnerabilities?query=CVE-2026").json()["total"] == 0
    assert hidden not in response.text


def test_scan_counts_and_recent_tasks_require_all_evidence_assets(
    client: TestClient, database_session: Session,
) -> None:
    from src.models.asset import Asset

    user, allowed, hidden = _context(client, database_session)
    now = datetime.now(timezone.utc)
    visible = _task("visible-scan", "visible scan", ScanStatus.COMPLETED, now, 0)
    visible.asset_ids = [allowed]
    mixed = _task("mixed-scan", "hidden mixed scan", ScanStatus.FAILED, now, 0)
    mixed.asset_ids = [allowed]
    mixed.asset_snapshot = [{"id": hidden}]
    persistent = _task("persistent-scan", "hidden persisted evidence", ScanStatus.FAILED, now, 0)
    persistent.asset_ids = [allowed]
    hidden_task = _task("hidden-scan", "hidden scan", ScanStatus.SCANNING, now, 0)
    hidden_task.asset_ids = [hidden]
    database_session.add_all([visible, mixed, persistent, hidden_task])
    database_session.flush()
    database_session.add(_finding(
        persistent, database_session.get(Asset, hidden), "CVE-2026-1234",
        "high", "confirmed", now,
    ))
    database_session.commit()
    _sign_in(client, user)
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scans"]["total"] == 1
    assert body["scans"]["running"] == 0
    assert body["operations"]["failed_tasks"] == 0
    assert [item["id"] for item in body["recent_scans"]] == [visible.id]
    assert not body["attention_items"]
    assert body["vulnerabilities"]["total"] == 0
    assert "hidden" not in response.text


def test_unlink_revocation_and_disabling_are_reflected_without_global_fallback(
    client: TestClient, database_session: Session,
) -> None:
    user, allowed, _ = _context(client, database_session)
    _sign_in(client, user)
    assert client.get("/api/v1/dashboard").json()["catalog"]["services"] == 1
    relation = database_session.query(CmdbServiceAsset).filter_by(asset_id=allowed).one()
    relation.status = ServiceAssetStatus.UNLINKED
    database_session.commit()
    assert client.get("/api/v1/dashboard").json()["catalog"]["services"] == 0
    assert client.get("/api/v1/observability/services").json()["services"] == []
    assert client.get("/api/v1/cmdb/tree?query=SHARED").json()["businesses"] == []
    account = database_session.get(User, user["id"])
    account.enabled = False
    database_session.commit()
    for path in ("/api/v1/dashboard", "/api/v1/assets", "/api/v1/cmdb/tree",
                 "/api/v1/vulnerabilities", "/api/v1/observability/services"):
        assert client.get(path).status_code == 401


def test_administrator_keeps_global_snapshot(
    client: TestClient, database_session: Session,
) -> None:
    _, _, hidden = _context(client, database_session)
    body = client.get("/api/v1/dashboard", headers=ADMIN).json()
    assert body["assets"]["total"] == 2
    assert body["service_health"]["services"][0]["asset_count"] == 2
    assert hidden in str(body["monitoring_assets"])


def test_archived_authorized_asset_does_not_reveal_catalog_via_dashboard(
    client: TestClient, database_session: Session,
) -> None:
    from src.models.asset import Asset

    user, allowed, _ = _context(client, database_session)
    database_session.get(Asset, allowed).lifecycle_status = AssetLifecycleStatus.ARCHIVED
    database_session.commit()
    _sign_in(client, user)
    body = client.get("/api/v1/dashboard").json()
    assert body["assets"]["total"] == 0
    assert body["catalog"]["services"] == 0
    assert body["service_health"]["services"] == []
