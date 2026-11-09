from unittest.mock import patch

from fastapi.testclient import TestClient

from src.models.ssh_host_key import SshHostKey
from src.models.ssh_host_key_audit import SshHostKeyAudit
from src.services.ssh_host_keys import HostKeyCandidate


HOST_KEY = HostKeyCandidate(
    key_type="ssh-ed25519",
    fingerprint="SHA256:cleanupFingerprintForAutomatedTest1234567890",
    public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICleanupTestKey",
)


def _asset(client: TestClient, environment: str = "test") -> dict[str, object]:
    response = client.post(
        "/api/v1/assets",
        json={
            "name": f"lifecycle-{environment}",
            "host": "192.0.2.80",
            "ssh_port": 22,
            "os_family": "openeuler",
            "environment": environment,
            "group_name": "lifecycle",
            "credential_id": None,
            "labels": ["test-data"],
        },
    )
    assert response.status_code == 201
    return response.json()


def _task(client: TestClient, asset_id: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "lifecycle scan",
            "scan_type": "host_vulnerability",
            "asset_ids": [asset_id],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_asset_lifecycle_filters_and_blocks_new_operations(client: TestClient) -> None:
    asset = _asset(client)
    archived = client.patch(
        f"/api/v1/assets/{asset['id']}/lifecycle",
        json={"status": "archived", "reason": "测试资产下线", "operator": "admin"},
    )

    assert archived.status_code == 200
    assert archived.json()["lifecycle_status"] == "archived"
    assert client.get("/api/v1/assets").json() == []
    assert client.get("/api/v1/assets?lifecycle=archived").json()[0]["id"] == asset["id"]
    assert client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "blocked scan",
            "scan_type": "host_vulnerability",
            "asset_ids": [asset["id"]],
        },
    ).status_code == 400
    assert client.post(f"/api/v1/monitoring/assets/{asset['id']}/collect").status_code == 409
    assert client.post(
        "/api/v1/terminal/tickets",
        json={"asset_id": asset["id"], "operator": "admin"},
    ).status_code == 409

    restored = client.patch(
        f"/api/v1/assets/{asset['id']}/lifecycle",
        json={"status": "enabled", "reason": "重新启用", "operator": "admin"},
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


def test_terminal_scan_task_can_be_archived_and_restored(client: TestClient) -> None:
    asset = _asset(client)
    task = _task(client, str(asset["id"]))
    client.post(f"/api/v1/scan-tasks/{task['id']}/cancel")

    archived = client.patch(
        f"/api/v1/scan-tasks/{task['id']}/archive",
        json={"archived": True, "operator": "admin"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert client.get("/api/v1/scan-tasks").json() == []
    assert client.get("/api/v1/scan-tasks?archive_view=archived").json()[0]["id"] == task["id"]
    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["scans"]["total"] == 0
    assert dashboard["recent_scans"] == []

    restored = client.patch(
        f"/api/v1/scan-tasks/{task['id']}/archive",
        json={"archived": False, "operator": "admin"},
    )
    assert restored.status_code == 200
    assert restored.json()["archived"] is False


def test_test_asset_cleanup_requires_preview_and_confirmation(client: TestClient) -> None:
    asset = _asset(client)
    preview = client.get(f"/api/v1/assets/{asset['id']}/cleanup-preview")
    assert preview.status_code == 200
    assert preview.json()["allowed"] is True
    assert preview.json()["confirmation_phrase"] == f"DELETE {asset['id']}"

    rejected = client.post(
        f"/api/v1/assets/{asset['id']}/cleanup",
        json={"reason": "清理自动化测试数据", "operator": "admin", "confirmation": "wrong"},
    )
    assert rejected.status_code == 409

    removed = client.post(
        f"/api/v1/assets/{asset['id']}/cleanup",
        json={
            "reason": "清理自动化测试数据",
            "operator": "admin",
            "confirmation": f"  DELETE {asset['id']}  ",
        },
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "deleted"
    assert client.get(f"/api/v1/assets/{asset['id']}/cleanup-preview").status_code == 404
    audits = client.get("/api/v1/assets/cleanup-audits").json()
    assert audits[0]["asset_id"] == asset["id"]


def test_cleanup_removes_registration_but_preserves_host_key_audit(
    client: TestClient, database_session,
) -> None:
    asset = _asset(client)
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=HOST_KEY):
        response = client.post(
            f"/api/v1/assets/{asset['id']}/ssh-host-key/confirm",
            json={"fingerprint": HOST_KEY.fingerprint},
        )
    assert response.status_code == 200

    removed = client.post(
        f"/api/v1/assets/{asset['id']}/cleanup",
        json={
            "reason": "清理含主机指纹的测试资产", "operator": "admin",
            "confirmation": f"DELETE {asset['id']}",
        },
    )

    assert removed.status_code == 200
    assert database_session.query(SshHostKey).filter_by(asset_id=asset["id"]).count() == 0
    audit = database_session.query(SshHostKeyAudit).filter_by(asset_id=asset["id"]).one()
    assert audit.asset_name == asset["name"]
    assert audit.host == asset["host"]


def test_cleanup_rejects_production_and_shared_scan_history(client: TestClient) -> None:
    production = _asset(client, "production")
    production_preview = client.get(
        f"/api/v1/assets/{production['id']}/cleanup-preview"
    ).json()
    assert production_preview["allowed"] is False

    test_asset = _asset(client)
    other = client.post(
        "/api/v1/assets",
        json={
            "name": "lifecycle-other", "host": "192.0.2.81", "ssh_port": 22,
            "os_family": "openeuler", "environment": "test", "group_name": "lifecycle",
            "credential_id": None, "labels": [],
        },
    ).json()
    response = client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "shared scan",
            "scan_type": "comprehensive",
            "asset_ids": [test_asset["id"], other["id"]],
        },
    )
    assert response.status_code == 201
    preview = client.get(f"/api/v1/assets/{test_asset['id']}/cleanup-preview").json()
    assert preview["allowed"] is False
    assert preview["shared_scan_tasks"] == 1
