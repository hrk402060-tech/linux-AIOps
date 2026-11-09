from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from src.models.baseline import BaselineTask
from src.models.exposure import ExposureTask
from src.models.report import ScanReport
from src.models.scan_task import ScanSchedule, ScanStatus, ScanTask
from src.models.vulnerability import VulnerabilityFinding
from test_asset_scope_reports_baselines_exposures import (
    ADMIN, _assign_asset, _create_asset, _create_completed_scan, _create_user, _sign_in,
)
from test_asset_scope_scan_cmdb import _create_service


@pytest.mark.parametrize("source", ["snapshot", "findings", "persisted_finding"])
def test_report_creation_checks_evidence_before_generation(
    client: TestClient, source: str,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    scan_id = _create_completed_scan(client, allowed, "inconsistent evidence")
    with client.app.state.session_factory() as session:
        task = session.get(ScanTask, scan_id)
        assert task is not None
        if source == "snapshot":
            task.asset_snapshot = [*task.asset_snapshot, {"id": hidden, "name": "hidden"}]
        elif source == "findings":
            task.findings = [{"asset_id": hidden, "title": "hidden evidence"}]
        else:
            session.add(VulnerabilityFinding(
                id=str(uuid4()), scan_task_id=scan_id, scan_task_name=task.name,
                asset_id=hidden, asset_name="hidden", cve_id="CVE-2026-0001",
                advisory_id="hidden-advisory", title="hidden evidence", severity="high",
                package_name="example", installed_version="1", fixed_version="2",
                source_name="scope-test", conclusion="affected", confidence="high",
                evidence_chain=[],
            ))
        session.commit()
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    with patch("src.services.reports.generate_report") as generate:
        response = client.post("/api/v1/reports", json={
            "scan_task_id": scan_id, "format": "docx", "operator": "operator",
        })
        assert response.status_code == 403
        generate.assert_not_called()
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ScanReport)) == 0


@pytest.mark.parametrize("resource, model", [
    ("baselines", BaselineTask), ("exposures", ExposureTask), ("scan-tasks", ScanTask),
])
def test_task_evidence_cannot_bypass_asset_filter(
    client: TestClient, resource: str,
    model: type[BaselineTask] | type[ExposureTask] | type[ScanTask],
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    payload = {"name": "inconsistent task", "asset_ids": [allowed]}
    if resource == "scan-tasks":
        payload["scan_type"] = "comprehensive"
    created = client.post(f"/api/v1/{resource}", headers=ADMIN, json=payload)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    with client.app.state.session_factory() as session:
        task = session.get(model, task_id)
        assert task is not None
        task.asset_snapshot = [*task.asset_snapshot, {"id": hidden, "name": "hidden"}]
        session.commit()
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get(f"/api/v1/{resource}").json() == []
    assert client.get(f"/api/v1/{resource}/{task_id}").status_code == 403


def test_mixed_schedule_cannot_be_toggled_or_created(client: TestClient) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    payload = {
        "name": "mixed schedule", "scan_type": "comprehensive",
        "asset_ids": [allowed, hidden], "schedule_kind": "interval",
        "interval_hours": 6, "timezone": "Asia/Shanghai",
    }
    created = client.post("/api/v1/scan-schedules", headers=ADMIN, json=payload)
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get("/api/v1/scan-schedules").json() == []
    assert client.patch(f"/api/v1/scan-schedules/{schedule_id}/enabled",
                        json={"enabled": False}).status_code == 403
    assert client.post("/api/v1/scan-schedules", json=payload).status_code == 403
    with client.app.state.session_factory() as session:
        schedule = session.get(ScanSchedule, schedule_id)
        assert schedule is not None and schedule.enabled
        schedule.asset_ids = [allowed]
        session.commit()
    assert client.get("/api/v1/scan-schedules").json() == []
    assert client.patch(f"/api/v1/scan-schedules/{schedule_id}/enabled",
                        json={"enabled": False}).status_code == 403


def test_cmdb_shared_children_and_historical_refs_do_not_grant_write(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    service_id = _create_service(client, "SHARED-HISTORY")
    for asset_id in (allowed, hidden):
        relation = client.post(f"/api/v1/cmdb/services/{service_id}/assets",
                               headers=ADMIN, json={"asset_id": asset_id})
        assert relation.status_code == 201
        if asset_id == hidden:
            assert client.patch(
                f"/api/v1/cmdb/service-assets/{relation.json()['id']}",
                headers=ADMIN, json={"status": "unlinked"},
            ).status_code == 200
    api = client.post("/api/v1/cmdb/apis", headers=ADMIN, json={
        "service_id": service_id, "code": "API", "name": "shared API",
    })
    assert api.status_code == 201, api.text
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    service = client.get(f"/api/v1/cmdb/services/{service_id}")
    assert service.status_code == 200
    application_id, business_id = (
        service.json()["application_id"], service.json()["business_id"]
    )
    assert client.get(f"/api/v1/cmdb/apis/{api.json()['id']}").status_code == 200
    assert client.patch(f"/api/v1/cmdb/apis/{api.json()['id']}",
                        json={"name": "forbidden"}).status_code == 403
    for path, payload in (
        ("/applications", {"business_id": business_id, "code": "NEW", "name": "new"}),
        ("/services", {"application_id": application_id, "code": "NEW", "name": "new"}),
        (f"/services/{service_id}/assets", {"asset_id": allowed}),
    ):
        assert client.post(f"/api/v1/cmdb{path}", json=payload).status_code == 403
    assert client.get(f"/api/v1/cmdb/services/{service_id}").json()["name"] == service.json()["name"]


def test_scoped_cmdb_owned_metadata_remains_editable(client: TestClient) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    service_id = _create_service(client, "OWNED")
    relation = client.post(f"/api/v1/cmdb/services/{service_id}/assets",
                           headers=ADMIN, json={"asset_id": allowed})
    assert relation.status_code == 201
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.patch(f"/api/v1/cmdb/services/{service_id}",
                        json={"name": "permitted change"}).status_code == 200
    assert client.post("/api/v1/cmdb/apis", json={
        "service_id": service_id, "code": "OWNED-API", "name": "permitted API",
    }).status_code == 201
    assert client.patch(f"/api/v1/cmdb/service-assets/{relation.json()['id']}",
                        json={"status": "unlinked"}).status_code == 200
    assert client.get(f"/api/v1/cmdb/services/{service_id}").status_code == 403


def test_no_asset_context_is_not_a_global_task_grant(client: TestClient) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    scan_id = _create_completed_scan(client, allowed, "no context")
    with client.app.state.session_factory() as session:
        task = session.get(ScanTask, scan_id)
        assert task is not None
        task.asset_ids, task.asset_snapshot = [], []
        task.status = ScanStatus.QUEUED
        session.commit()
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get("/api/v1/scan-tasks").json() == []
    assert client.post(f"/api/v1/scan-tasks/{scan_id}/start").status_code == 403
    assert client.get(f"/api/v1/scan-tasks/{scan_id}", headers=ADMIN).status_code == 200


def test_scoped_auditor_remains_read_only_and_revocation_takes_effect(
    client: TestClient,
) -> None:
    response = client.post("/api/v1/auth/users", headers=ADMIN, json={
        "username": "scope-auditor", "password": "Scope-Test-2026!", "role": "auditor",
    })
    assert response.status_code == 201, response.text
    user = response.json()
    allowed = _create_asset(client, "allowed-node")
    scan_id = _create_completed_scan(client, allowed, "auditor task")
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get(f"/api/v1/scan-tasks/{scan_id}").status_code == 200
    assert client.post(f"/api/v1/scan-tasks/{scan_id}/rerun").status_code == 403
    assert client.post("/api/v1/reports", json={
        "scan_task_id": scan_id, "format": "docx", "operator": "auditor",
    }).status_code == 403
    assert client.request("DELETE", f"/api/v1/scan-tasks/{scan_id}", json={
        "reason": "forbidden", "confirmation": f"DELETE {scan_id}",
    }).status_code == 403
    current = client.get(f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN)
    revoked = client.put(f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN, json={
        "asset_scope": "assigned", "entries": [],
        "expected_revision": current.json()["revision"],
        "confirmation": user["username"], "reason": "revoke asset",
    })
    assert revoked.status_code == 200, revoked.text
    assert client.get(f"/api/v1/scan-tasks/{scan_id}").status_code == 403
