import base64
from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.config import Settings
from src.main import create_app


@pytest.fixture
def auth_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'cmdb.db'}",
        credential_master_key=base64.b64encode(bytes(range(32))).decode("ascii"),
        credential_key_version="test-v1",
        api_admin_token="test-admin-token-with-at-least-32-characters",
        auto_create_schema=True,
        auth_bootstrap_username="security-admin",
        auth_bootstrap_password="Correct-Horse-Battery-2026",
        auth_cookie_secure=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def _activate_admin(client: TestClient) -> None:
    _login(client, "security-admin", "Correct-Horse-Battery-2026")
    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "Correct-Horse-Battery-2026",
            "new_password": "Active-Admin-Password-2026",
        },
    )
    assert response.status_code == 200


def _create_business(client: TestClient, code: str = "PAY") -> dict[str, object]:
    response = client.post(
        "/api/v1/cmdb/businesses",
        json={"code": code, "name": "支付业务", "description": "核心支付链路"},
    )
    assert response.status_code == 201
    return response.json()


def _create_hierarchy(client: TestClient) -> tuple[dict[str, object], ...]:
    business = _create_business(client)
    application_response = client.post(
        "/api/v1/cmdb/applications",
        json={
            "business_id": business["id"],
            "code": "GATEWAY",
            "name": "支付网关",
        },
    )
    assert application_response.status_code == 201
    application = application_response.json()
    service_response = client.post(
        "/api/v1/cmdb/services",
        json={
            "application_id": application["id"],
            "code": "PAY-API",
            "name": "支付 API 服务",
            "service_type": "http",
        },
    )
    assert service_response.status_code == 201
    service = service_response.json()
    api_response = client.post(
        "/api/v1/cmdb/apis",
        json={
            "service_id": service["id"],
            "code": "CREATE-ORDER",
            "name": "创建订单",
            "http_method": "POST",
            "path": "/orders",
        },
    )
    assert api_response.status_code == 201
    return business, application, service, api_response.json()


def test_create_hierarchy_tree_and_server_side_audit(
    auth_client: TestClient,
) -> None:
    _activate_admin(auth_client)
    business, application, service, api = _create_hierarchy(auth_client)

    tree = auth_client.get("/api/v1/cmdb/tree").json()["businesses"]

    assert tree[0]["id"] == business["id"]
    assert tree[0]["applications"][0]["id"] == application["id"]
    assert tree[0]["applications"][0]["services"][0]["id"] == service["id"]
    assert tree[0]["applications"][0]["services"][0]["apis"][0]["id"] == api["id"]
    audit = auth_client.get("/api/v1/audit-log?source=cmdb_change").json()
    assert audit["total"] == 4
    assert {item["operator"] for item in audit["items"]} == {"security-admin"}


def test_codes_are_unique_in_their_documented_scope(auth_client: TestClient) -> None:
    _activate_admin(auth_client)
    business, application, service, _ = _create_hierarchy(auth_client)

    duplicate_business = auth_client.post(
        "/api/v1/cmdb/businesses", json={"code": " pay ", "name": "重复业务"}
    )
    duplicate_application = auth_client.post(
        "/api/v1/cmdb/applications",
        json={"business_id": business["id"], "code": "gateway", "name": "重复应用"},
    )
    duplicate_service = auth_client.post(
        "/api/v1/cmdb/services",
        json={"application_id": application["id"], "code": "pay-api", "name": "重复服务"},
    )
    duplicate_api = auth_client.post(
        "/api/v1/cmdb/apis",
        json={"service_id": service["id"], "code": "create-order", "name": "重复接口"},
    )

    assert {duplicate_business.status_code, duplicate_application.status_code,
            duplicate_service.status_code, duplicate_api.status_code} == {409}


def test_archived_parent_rejects_new_child_and_admin_can_restore(
    auth_client: TestClient,
) -> None:
    _activate_admin(auth_client)
    business, application, service, _ = _create_hierarchy(auth_client)
    archived = auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"lifecycle_status": "archived", "lifecycle_reason": "业务下线"},
    )
    assert archived.status_code == 200
    rejected = auth_client.post(
        "/api/v1/cmdb/applications",
        json={"business_id": business["id"], "code": "NEW", "name": "新应用"},
    )
    assert rejected.status_code == 409
    nested_service_rejected = auth_client.post(
        "/api/v1/cmdb/services",
        json={"application_id": application["id"], "code": "NEW-SVC", "name": "新服务"},
    )
    assert nested_service_rejected.status_code == 409
    nested_update_rejected = auth_client.patch(
        f"/api/v1/cmdb/services/{service['id']}", json={"name": "归档域内修改"},
    )
    assert nested_update_rejected.status_code == 409
    nested_rejected = auth_client.post(
        "/api/v1/cmdb/apis",
        json={"service_id": service["id"], "code": "NESTED", "name": "嵌套接口"},
    )
    assert nested_rejected.status_code == 409
    restored = auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"lifecycle_status": "active", "lifecycle_reason": "重新启用"},
    )
    assert restored.status_code == 200
    assert restored.json()["lifecycle_status"] == "active"


def test_service_asset_relation_is_durable_and_duplicate_is_rejected(
    auth_client: TestClient,
) -> None:
    _activate_admin(auth_client)
    _, _, service, _ = _create_hierarchy(auth_client)
    asset = auth_client.post(
        "/api/v1/assets",
        json={"name": "pay-node-01", "host": "10.10.1.20", "os_family": "openeuler"},
    ).json()
    linked = auth_client.post(
        f"/api/v1/cmdb/services/{service['id']}/assets",
        json={"asset_id": asset["id"], "relation_role": "primary"},
    )
    duplicate = auth_client.post(
        f"/api/v1/cmdb/services/{service['id']}/assets",
        json={"asset_id": asset["id"], "relation_role": "replica"},
    )
    assert linked.status_code == 201
    assert linked.json()["business_id"]
    assert linked.json()["asset_name"] == "pay-node-01"
    assert duplicate.status_code == 409

    unlinked = auth_client.patch(
        f"/api/v1/cmdb/service-assets/{linked.json()['id']}",
        json={"status": "unlinked"},
    )
    assert unlinked.status_code == 200
    assert unlinked.json()["unlinked_at"] is not None
    service_history = auth_client.get(
        f"/api/v1/cmdb/services/{service['id']}"
    ).json()
    tree_history = auth_client.get("/api/v1/cmdb/tree").json()["businesses"][0]
    assert service_history["id"] == service["id"]
    assert tree_history["applications"][0]["services"][0]["assets"][0]["status"] == "unlinked"
    assert auth_client.get("/api/v1/assets").json()[0]["id"] == asset["id"]


def test_relation_restore_revalidates_hierarchy_and_asset_lifecycle(
    auth_client: TestClient,
) -> None:
    _activate_admin(auth_client)
    business, _, service, _ = _create_hierarchy(auth_client)
    asset = auth_client.post(
        "/api/v1/assets",
        json={"name": "restore-node", "host": "10.10.1.21", "os_family": "openeuler"},
    ).json()
    linked = auth_client.post(
        f"/api/v1/cmdb/services/{service['id']}/assets",
        json={"asset_id": asset["id"], "relation_role": "primary"},
    ).json()
    assert auth_client.patch(
        f"/api/v1/cmdb/service-assets/{linked['id']}", json={"status": "unlinked"},
    ).status_code == 200

    assert auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"lifecycle_status": "archived", "lifecycle_reason": "业务下线"},
    ).status_code == 200
    assert auth_client.patch(
        f"/api/v1/cmdb/service-assets/{linked['id']}", json={"status": "active"},
    ).status_code == 409

    assert auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"lifecycle_status": "active", "lifecycle_reason": "业务恢复"},
    ).status_code == 200
    assert auth_client.patch(
        f"/api/v1/assets/{asset['id']}/lifecycle",
        json={"status": "archived", "reason": "服务器退役"},
    ).status_code == 200
    assert auth_client.patch(
        f"/api/v1/cmdb/service-assets/{linked['id']}", json={"status": "active"},
    ).status_code == 409


def test_auditor_is_read_only_and_operator_cannot_archive(
    auth_client: TestClient,
) -> None:
    _activate_admin(auth_client)
    business = _create_business(auth_client)
    for username, role in (("ops-user", "operator"), ("audit-user", "auditor")):
        created = auth_client.post(
            "/api/v1/auth/users",
            json={
                "username": username,
                "password": f"{role.title()}-Password-2026",
                "role": role,
                "asset_scope": "all",
            },
        )
        assert created.status_code == 201
    auth_client.post("/api/v1/auth/logout")
    _login(auth_client, "ops-user", "Operator-Password-2026")
    assert auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"name": "支付业务平台"},
    ).status_code == 200
    assert auth_client.patch(
        f"/api/v1/cmdb/businesses/{business['id']}",
        json={"lifecycle_status": "archived", "lifecycle_reason": "越权归档"},
    ).status_code == 403
    auth_client.post("/api/v1/auth/logout")
    _login(auth_client, "audit-user", "Auditor-Password-2026")
    assert auth_client.get("/api/v1/cmdb/tree").status_code == 200
    assert auth_client.post(
        "/api/v1/cmdb/businesses", json={"code": "DENIED", "name": "拒绝"}
    ).status_code == 403
