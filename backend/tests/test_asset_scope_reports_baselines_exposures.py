from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from unittest.mock import patch

import pytest

from src.models.report import ScanReport
from src.models.scan_task import ScanStatus, ScanTask, ScanType

ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _create_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/users",
        headers=ADMIN,
        json={
            "username": "asset-scope-reports",
            "password": "Scope-Test-2026!",
            "role": "operator",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_asset(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/v1/assets",
        headers=ADMIN,
        json={
            "name": name,
            "host": f"192.0.2.{10 if name == 'allowed-node' else 11}",
            "ssh_port": 22,
            "os_family": "openeuler",
            "environment": "test",
            "group_name": "scope",
            "labels": [],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _assign_asset(client: TestClient, user: dict[str, object], asset_id: str) -> None:
    current = client.get(
        f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN
    )
    assert current.status_code == 200, current.text
    response = client.put(
        f"/api/v1/auth/users/{user['id']}/asset-access",
        headers=ADMIN,
        json={
            "asset_scope": "assigned",
            "expected_revision": current.json()["revision"],
            "entries": [{"asset_id": asset_id, "can_ssh": False}],
            "confirmation": user["username"],
            "reason": "scope test",
        },
    )
    assert response.status_code == 200, response.text


def _sign_in(client: TestClient, user: dict[str, object]) -> None:
    client.headers.pop("authorization", None)
    client.headers.pop("Authorization", None)
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": user["username"], "password": "Scope-Test-2026!"},
    )
    assert response.status_code == 200, response.text


def _create_completed_scan(client: TestClient, asset_id: str, name: str) -> str:
    scan_id = str(uuid4())
    with client.app.state.session_factory() as session:
        session.add(
            ScanTask(
                id=scan_id,
                name=name,
                scan_type=ScanType.COMPREHENSIVE,
                status=ScanStatus.COMPLETED,
                progress=100,
                read_only=True,
                asset_ids=[asset_id],
                asset_snapshot=[
                    {
                        "id": asset_id,
                        "name": name,
                        "host": "192.0.2.10",
                        "os_family": "openeuler",
                        "environment": "test",
                    }
                ],
                knowledge_version="scope-test",
                findings=[],
                result_summary={
                    "assets_total": 1,
                    "assets_scanned": 1,
                    "assets_failed": 0,
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "manual_confirmation": 0,
                },
                report_content="scope report",
                finished_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return scan_id


def test_reports_are_filtered_and_mutations_require_asset_access(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed_asset = _create_asset(client, "allowed-node")
    hidden_asset = _create_asset(client, "hidden-node")
    allowed_scan = _create_completed_scan(client, allowed_asset, "allowed scan")
    hidden_scan = _create_completed_scan(client, hidden_asset, "hidden scan")
    allowed_report = client.post(
        "/api/v1/reports", headers=ADMIN,
        json={"scan_task_id": allowed_scan, "format": "docx", "operator": "admin"},
    )
    hidden_report = client.post(
        "/api/v1/reports", headers=ADMIN,
        json={"scan_task_id": hidden_scan, "format": "docx", "operator": "admin"},
    )
    assert allowed_report.status_code == 201, allowed_report.text
    assert hidden_report.status_code == 201, hidden_report.text
    _assign_asset(client, user, allowed_asset)
    _sign_in(client, user)

    listing = client.get("/api/v1/reports")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [allowed_report.json()["id"]]
    assert client.get(f"/api/v1/reports/{allowed_report.json()['id']}").status_code == 200
    assert client.get(f"/api/v1/reports/{hidden_report.json()['id']}").status_code == 403
    assert client.get(f"/api/v1/reports/{hidden_report.json()['id']}/download").status_code == 403
    assert client.post(
        "/api/v1/reports",
        json={"scan_task_id": hidden_scan, "format": "docx", "operator": "operator"},
    ).status_code == 403
    assert client.post(
        f"/api/v1/reports/{hidden_report.json()['id']}/retry",
        json={"operator": "operator"},
    ).status_code == 403


def test_baseline_and_exposure_tasks_are_filtered_and_protected(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed_asset = _create_asset(client, "allowed-node")
    hidden_asset = _create_asset(client, "hidden-node")
    baseline_allowed = client.post(
        "/api/v1/baselines", headers=ADMIN,
        json={"name": "allowed baseline", "asset_ids": [allowed_asset]},
    )
    baseline_hidden = client.post(
        "/api/v1/baselines", headers=ADMIN,
        json={"name": "hidden baseline", "asset_ids": [hidden_asset]},
    )
    exposure_allowed = client.post(
        "/api/v1/exposures", headers=ADMIN,
        json={"name": "allowed exposure", "asset_ids": [allowed_asset]},
    )
    exposure_hidden = client.post(
        "/api/v1/exposures", headers=ADMIN,
        json={"name": "hidden exposure", "asset_ids": [hidden_asset]},
    )
    for response in (baseline_allowed, baseline_hidden, exposure_allowed, exposure_hidden):
        assert response.status_code == 201, response.text
    _assign_asset(client, user, allowed_asset)
    _sign_in(client, user)

    assert [item["id"] for item in client.get("/api/v1/baselines").json()] == [
        baseline_allowed.json()["id"]
    ]
    assert [item["id"] for item in client.get("/api/v1/exposures").json()] == [
        exposure_allowed.json()["id"]
    ]
    hidden_get_paths = (
        f"/api/v1/baselines/{baseline_hidden.json()['id']}",
        f"/api/v1/exposures/{exposure_hidden.json()['id']}",
    )
    hidden_post_paths = (
        f"/api/v1/baselines/{baseline_hidden.json()['id']}/start",
        f"/api/v1/baselines/{baseline_hidden.json()['id']}/cancel",
        f"/api/v1/baselines/{baseline_hidden.json()['id']}/rerun",
        f"/api/v1/exposures/{exposure_hidden.json()['id']}/start",
        f"/api/v1/exposures/{exposure_hidden.json()['id']}/cancel",
        f"/api/v1/exposures/{exposure_hidden.json()['id']}/rerun",
    )
    for path in hidden_get_paths:
        assert client.get(path).status_code == 403
    for path in hidden_post_paths:
        assert client.post(path).status_code == 403
    assert client.post(
        "/api/v1/baselines",
        json={"name": "blocked baseline", "asset_ids": [hidden_asset]},
    ).status_code == 403
    assert client.post(
        "/api/v1/exposures",
        json={"name": "blocked exposure", "asset_ids": [hidden_asset]},
    ).status_code == 403


def test_reports_require_both_snapshot_and_task_scope(client: TestClient) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    scan_id = _create_completed_scan(client, allowed, "mixed report")
    response = client.post("/api/v1/reports", headers=ADMIN, json={
        "scan_task_id": scan_id, "format": "docx", "operator": "admin",
    })
    assert response.status_code == 201
    report_id = response.json()["id"]
    with client.app.state.session_factory() as session:
        task = session.get(ScanTask, scan_id)
        assert task is not None
        task.asset_ids = [allowed, hidden]
        session.commit()
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get("/api/v1/reports").json() == []
    assert client.get(f"/api/v1/reports/{report_id}/download").status_code == 403
    with client.app.state.session_factory() as session:
        report = session.get(ScanReport, report_id)
        assert report is not None
        assert report.download_count == 0
        report.asset_snapshot = []
        report.finding_snapshot = []
        task = session.get(ScanTask, scan_id)
        assert task is not None
        task.asset_ids = []
        task.asset_snapshot = []
        session.commit()
    assert client.get("/api/v1/reports").json() == []
    assert client.get(f"/api/v1/reports/{report_id}").status_code == 403
    assert client.get(f"/api/v1/reports/{report_id}", headers=ADMIN).status_code == 200


@pytest.mark.parametrize("resource", ["baselines", "exposures"])
def test_mixed_tasks_are_not_partially_visible_or_executable(
    client: TestClient, resource: str,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    created = client.post(f"/api/v1/{resource}", headers=ADMIN, json={
        "name": "mixed asset task", "asset_ids": [allowed, hidden],
    })
    assert created.status_code == 201
    task_id = created.json()["id"]
    _assign_asset(client, user, allowed)
    _sign_in(client, user)
    assert client.get(f"/api/v1/{resource}").json() == []
    with patch(f"src.services.{resource}.execute_task") as execute:
        assert client.post(f"/api/v1/{resource}/{task_id}/start").status_code == 403
        execute.assert_not_called()
    assert client.post(f"/api/v1/{resource}", json={
        "name": "allowed only task", "asset_ids": [allowed],
    }).status_code == 201
