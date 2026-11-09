from unittest.mock import patch

from fastapi.testclient import TestClient


def create_credential(client: TestClient) -> str:
    response = client.post(
        "/api/v1/credentials",
        json={
            "name": "Asset reader",
            "credential_type": "password",
            "username": "ops-reader",
            "secret": "never-return-this-secret",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_asset_payload(credential_id: str | None = None) -> dict[str, object]:
    return {
        "name": "openEuler production",
        "host": "server.example.internal",
        "ssh_port": 22,
        "os_family": "openeuler",
        "environment": "production",
        "group_name": "核心业务",
        "credential_id": credential_id,
        "labels": ["database", "critical"],
    }


def test_create_and_list_asset_with_masked_credential(client: TestClient) -> None:
    credential_id = create_credential(client)

    created = client.post(
        "/api/v1/assets", json=create_asset_payload(credential_id)
    )

    assert created.status_code == 201
    body = created.json()
    assert body["os_family"] == "openeuler"
    assert body["status"] == "unknown"
    assert body["credential"] == {
        "id": credential_id,
        "name": "Asset reader",
        "username": "ops-reader",
        "credential_type": "password",
    }
    assert "secret" not in created.text
    assert client.get("/api/v1/assets").json()[0]["name"] == body["name"]


def test_asset_rejects_invalid_host_and_credential(client: TestClient) -> None:
    invalid_host = client.post(
        "/api/v1/assets", json={**create_asset_payload(), "host": "https://host"}
    )
    missing_credential = client.post(
        "/api/v1/assets",
        json=create_asset_payload("00000000-0000-0000-0000-000000000000"),
    )

    assert invalid_host.status_code == 422
    assert missing_credential.status_code == 400


def test_update_and_archive_asset(client: TestClient) -> None:
    asset_id = client.post("/api/v1/assets", json=create_asset_payload()).json()["id"]

    updated = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"name": "Kylin application", "os_family": "kylin"},
    )

    assert updated.status_code == 200
    assert updated.json()["os_family"] == "kylin"
    archived = client.patch(
        f"/api/v1/assets/{asset_id}/lifecycle",
        json={"status": "archived", "reason": "资产退役", "operator": "admin"},
    )
    assert archived.status_code == 200
    assert client.get("/api/v1/assets").json() == []


def test_connectivity_check_updates_status_without_credentials(client: TestClient) -> None:
    asset_id = client.post("/api/v1/assets", json=create_asset_payload()).json()["id"]

    with patch(
        "src.services.assets.check_tcp_connectivity",
        return_value=(True, 12, "SSH 端口可达"),
    ) as check:
        response = client.post(f"/api/v1/assets/{asset_id}/check")

    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["latency_ms"] == 12
    check.assert_called_once_with("server.example.internal", 22, 3.0)


def test_duplicate_asset_name_is_rejected(client: TestClient) -> None:
    first = client.post("/api/v1/assets", json=create_asset_payload())
    second = client.post("/api/v1/assets", json=create_asset_payload())

    assert first.status_code == 201
    assert second.status_code == 409


def test_credentials_report_asset_count_and_cannot_be_deleted_in_use(
    client: TestClient,
) -> None:
    credential_id = create_credential(client)
    client.post("/api/v1/assets", json=create_asset_payload(credential_id))

    credentials = client.get("/api/v1/credentials").json()

    assert credentials[0]["asset_count"] == 1
    response = client.delete(f"/api/v1/credentials/{credential_id}")
    assert response.status_code == 409
