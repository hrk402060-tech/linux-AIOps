from fastapi.testclient import TestClient
from unittest.mock import patch

import pytest

from src.models.scan_task import ScanStatus, ScanTask


ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _create_user(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/users",
        headers=ADMIN,
        json={
            "username": "scoped-cmdb-user",
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
            "ssh_username": "ops",
            "ssh_secret": "scope-only-secret",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _grant_asset(client: TestClient, user_id: str, asset_id: str) -> None:
    current = client.get(
        f"/api/v1/auth/users/{user_id}/asset-access", headers=ADMIN
    )
    assert current.status_code == 200
    response = client.put(
        f"/api/v1/auth/users/{user_id}/asset-access",
        headers=ADMIN,
        json={
            "asset_scope": "assigned",
            "expected_revision": current.json()["revision"],
            "entries": [{"asset_id": asset_id, "can_ssh": False}],
            "confirmation": "scoped-cmdb-user",
            "reason": "scope regression",
        },
    )
    assert response.status_code == 200, response.text


def _create_service(client: TestClient, code: str) -> str:
    business = client.post(
        "/api/v1/cmdb/businesses",
        headers=ADMIN,
        json={"code": f"{code}-B", "name": f"{code} business"},
    ).json()
    application = client.post(
        "/api/v1/cmdb/applications",
        headers=ADMIN,
        json={
            "business_id": business["id"],
            "code": f"{code}-A",
            "name": f"{code} application",
        },
    ).json()
    service = client.post(
        "/api/v1/cmdb/services",
        headers=ADMIN,
        json={
            "application_id": application["id"],
            "code": f"{code}-S",
            "name": f"{code} service",
        },
    )
    assert service.status_code == 201, service.text
    return service.json()["id"]


def _sign_in_scoped_user(client: TestClient) -> None:
    client.headers.pop("authorization", None)
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "scoped-cmdb-user", "password": "Scope-Test-2026!"},
    )
    assert response.status_code == 200, response.text


def test_scoped_user_cannot_read_or_create_foreign_scan_tasks_and_schedules(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    _grant_asset(client, user["id"], allowed)
    allowed_task = client.post(
        "/api/v1/scan-tasks",
        headers=ADMIN,
        json={"name": "allowed scan", "scan_type": "comprehensive", "asset_ids": [allowed]},
    ).json()
    hidden_task = client.post(
        "/api/v1/scan-tasks",
        headers=ADMIN,
        json={"name": "hidden scan", "scan_type": "comprehensive", "asset_ids": [hidden]},
    ).json()
    hidden_schedule = client.post(
        "/api/v1/scan-schedules",
        headers=ADMIN,
        json={
            "name": "hidden schedule", "scan_type": "comprehensive",
            "asset_ids": [hidden], "schedule_kind": "interval",
            "interval_hours": 6, "timezone": "Asia/Shanghai",
        },
    )
    assert hidden_schedule.status_code == 201, hidden_schedule.text

    _sign_in_scoped_user(client)
    listed = client.get("/api/v1/scan-tasks")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [allowed_task["id"]]
    assert client.get(f"/api/v1/scan-tasks/{hidden_task['id']}").status_code == 403
    assert client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "forbidden scan", "scan_type": "comprehensive",
            "asset_ids": [hidden],
        },
    ).status_code == 403
    assert client.get("/api/v1/scan-schedules").json() == []
    schedule_id = hidden_schedule.json()["id"]
    assert client.get(f"/api/v1/scan-schedules/{schedule_id}/tasks").status_code == 403


def test_cmdb_tree_and_relations_follow_authorized_asset_context(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    visible_service = _create_service(client, "VISIBLE")
    hidden_service = _create_service(client, "HIDDEN")
    visible_relation = client.post(
        f"/api/v1/cmdb/services/{visible_service}/assets",
        headers=ADMIN,
        json={"asset_id": allowed, "relation_role": "primary"},
    )
    hidden_relation = client.post(
        f"/api/v1/cmdb/services/{hidden_service}/assets",
        headers=ADMIN,
        json={"asset_id": hidden, "relation_role": "primary"},
    )
    assert visible_relation.status_code == 201
    assert hidden_relation.status_code == 201
    _grant_asset(client, user["id"], allowed)

    _sign_in_scoped_user(client)
    tree = client.get("/api/v1/cmdb/tree")
    assert tree.status_code == 200
    services = [
        service
        for business in tree.json()["businesses"]
        for application in business["applications"]
        for service in application["services"]
    ]
    assert [service["id"] for service in services] == [visible_service]
    assert client.get(f"/api/v1/cmdb/services/{visible_service}").status_code == 200
    assert client.get(f"/api/v1/cmdb/services/{hidden_service}").status_code == 403
    assert client.get(
        f"/api/v1/cmdb/service-assets?asset_id={hidden}"
    ).status_code == 403
    assert client.post(
        f"/api/v1/cmdb/services/{visible_service}/assets",
        json={"asset_id": hidden, "relation_role": "forbidden"},
    ).status_code == 403


def test_shared_cmdb_metadata_does_not_grant_shared_write_access(
    client: TestClient,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    service_id = _create_service(client, "SHARED")
    for asset_id in (allowed, hidden):
        assert client.post(f"/api/v1/cmdb/services/{service_id}/assets",
                           headers=ADMIN, json={"asset_id": asset_id}).status_code == 201
    orphan_id = _create_service(client, "UNLINKED")
    _grant_asset(client, user["id"], allowed)
    _sign_in_scoped_user(client)
    service = client.get(f"/api/v1/cmdb/services/{service_id}")
    assert service.status_code == 200
    application_id = service.json()["application_id"]
    business_id = service.json()["business_id"]
    assert client.get(f"/api/v1/cmdb/services/{orphan_id}").status_code == 403
    for kind, item_id in (("services", service_id), ("applications", application_id),
                          ("businesses", business_id)):
        assert client.patch(f"/api/v1/cmdb/{kind}/{item_id}",
                            json={"name": "unauthorized shared edit"}).status_code == 403
    relations = client.get(f"/api/v1/cmdb/service-assets?service_id={service_id}")
    assert relations.status_code == 200
    assert {row["asset_id"] for row in relations.json()} == {allowed}
    assert client.post("/api/v1/cmdb/apis", json={
        "service_id": service_id, "name": "blocked API", "code": "BLOCKED",
    }).status_code == 403
    assert client.post("/api/v1/cmdb/businesses", json={
        "code": "BLOCKED", "name": "blocked root",
    }).status_code == 403


@pytest.mark.parametrize("action", ["start", "cancel", "rerun", "report", "archive"])
def test_mixed_scan_tasks_reject_actions_before_side_effects(
    client: TestClient, action: str,
) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    hidden = _create_asset(client, "hidden-node")
    created = client.post("/api/v1/scan-tasks", headers=ADMIN, json={
        "name": "mixed scan", "scan_type": "comprehensive", "asset_ids": [allowed, hidden],
    })
    assert created.status_code == 201
    task_id = created.json()["id"]
    _grant_asset(client, user["id"], allowed)
    _sign_in_scoped_user(client)
    path = f"/api/v1/scan-tasks/{task_id}/{action}"
    with patch("src.services.scan_tasks.execute_task") as execute:
        if action == "report":
            response = client.get(path)
        elif action == "archive":
            response = client.patch(path, json={"archived": True})
        else:
            response = client.post(path)
        assert response.status_code == 403
        execute.assert_not_called()
    assert client.get("/api/v1/scan-tasks?archive_view=all").json() == []
    with client.app.state.session_factory() as session:
        task = session.get(ScanTask, task_id)
        assert task is not None and task.status == ScanStatus.QUEUED


def test_unlinked_relation_no_longer_grants_cmdb_access(client: TestClient) -> None:
    user = _create_user(client)
    allowed = _create_asset(client, "allowed-node")
    service_id = _create_service(client, "OLD")
    linked = client.post(f"/api/v1/cmdb/services/{service_id}/assets",
                         headers=ADMIN, json={"asset_id": allowed})
    assert linked.status_code == 201
    assert client.patch(f"/api/v1/cmdb/service-assets/{linked.json()['id']}",
                        headers=ADMIN, json={"status": "unlinked"}).status_code == 200
    _grant_asset(client, user["id"], allowed)
    _sign_in_scoped_user(client)
    assert client.get("/api/v1/cmdb/tree").json() == {"businesses": []}
    assert client.get(f"/api/v1/cmdb/services/{service_id}").status_code == 403
