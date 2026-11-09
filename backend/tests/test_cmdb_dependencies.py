from fastapi.testclient import TestClient


def _create_service(client: TestClient, code: str, name: str) -> dict[str, object]:
    business = client.post(
        "/api/v1/cmdb/businesses", json={"code": code, "name": f"{name}业务"},
    ).json()
    application = client.post(
        "/api/v1/cmdb/applications",
        json={"business_id": business["id"], "code": f"{code}APP", "name": f"{name}应用"},
    ).json()
    return client.post(
        "/api/v1/cmdb/services",
        json={
            "application_id": application["id"],
            "code": f"{code}SVC",
            "name": name,
            "service_type": "internal",
        },
    ).json()


def test_service_dependencies_are_audited_and_disableable(client: TestClient) -> None:
    source = _create_service(client, "SRC", "订单服务")
    target = _create_service(client, "TGT", "库存服务")

    created = client.post(
        f"/api/v1/cmdb/services/{source['id']}/dependencies",
        json={
            "target_service_id": target["id"],
            "dependency_type": " calls ",
            "description": "同步读取库存",
        },
    )
    assert created.status_code == 201
    dependency = created.json()
    assert dependency["dependency_type"] == "calls"
    assert dependency["source_service_name"] == "订单服务"
    assert dependency["target_service_name"] == "库存服务"

    duplicate = client.post(
        f"/api/v1/cmdb/services/{source['id']}/dependencies",
        json={"target_service_id": target["id"], "dependency_type": "calls"},
    )
    self_dependency = client.post(
        f"/api/v1/cmdb/services/{source['id']}/dependencies",
        json={"target_service_id": source["id"], "dependency_type": "calls"},
    )
    assert duplicate.status_code == 409
    assert self_dependency.status_code == 409

    listed = client.get(f"/api/v1/cmdb/services/{target['id']}/dependencies")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == dependency["id"]

    disabled = client.patch(
        f"/api/v1/cmdb/service-dependencies/{dependency['id']}",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    audit = client.get("/api/v1/audit-log?source=cmdb_change").json()
    assert any(item["action"] == "disable" for item in audit["items"])
