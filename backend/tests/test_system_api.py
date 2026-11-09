from fastapi.testclient import TestClient


def test_system_status_is_read_only_and_masks_security_boundaries(client: TestClient) -> None:
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["database_status"] == "ok"
    assert body["database_engine"] == "sqlite"
    assert body["security"] == {"scan_read_only_default": True, "ai_direct_execution": False, "credential_encryption": "AES-256-GCM", "authentication": "password_session"}
    assert body["terminal"]["host_key_verification"] is True
    assert body["diagnostic_scheduler"]["enabled"] is False
    assert body["diagnostic_scheduler"]["status"] == "disabled"
    assert body["diagnostic_scheduler"]["stale_after_seconds"] == 10
    assert body["diagnostic_scheduler"]["queued"] == 0
    assert "lease_token" not in body["diagnostic_scheduler"]
    assert "database_url" not in body
    assert body["health"] == {
        "status": "unknown",
        "is_stale": True,
        "stale_after_seconds": 300,
        "last_observed_at": None,
    }
