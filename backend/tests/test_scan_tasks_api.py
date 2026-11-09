from unittest.mock import patch

from fastapi.testclient import TestClient


def _create_credential(client: TestClient) -> str:
    response = client.post(
        "/api/v1/credentials",
        json={
            "name": "scan-reader", "credential_type": "password",
            "username": "audit", "secret": "server-only-secret",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_asset(
    client: TestClient, name: str = "scan-target", credential_id: str | None = None
) -> str:
    response = client.post(
        "/api/v1/assets",
        json={
            "name": name,
            "host": "192.0.2.10",
            "ssh_port": 22,
            "os_family": "openeuler",
            "environment": "test",
            "group_name": "security-lab",
            "credential_id": credential_id,
            "labels": ["readonly"],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_task(client: TestClient, asset_id: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "openEuler weekly assessment",
            "scan_type": "comprehensive",
            "asset_ids": [asset_id],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_scan_task_captures_read_only_asset_snapshot(client: TestClient) -> None:
    asset_id = _create_asset(client)

    task = _create_task(client, asset_id)

    assert task["status"] == "queued"
    assert task["progress"] == 0
    assert task["read_only"] is True
    assert task["asset_ids"] == [asset_id]
    assert task["asset_snapshot"][0]["os_family"] == "openeuler"
    assert task["knowledge_version"] is None
    assert client.get("/api/v1/scan-tasks").json()[0]["id"] == task["id"]


def test_scan_task_runs_full_state_machine_and_generates_report(
    client: TestClient,
) -> None:
    asset_id = _create_asset(client, credential_id=_create_credential(client))
    task = _create_task(client, asset_id)

    finding = {
        "cve_id": "CVE-2026-1001", "advisory_id": "openEuler-SA-2026-1001",
        "title": "测试公告", "severity": "high", "package_name": "kernel",
        "installed_version": "1", "fixed_version": "2",
        "source_name": "openEuler Security Advisories", "vendor_url": "https://example.test",
        "conclusion": "confirmed", "confidence": "high", "evidence_chain": [],
    }
    collected = {
        "asset_id": asset_id, "asset_name": "scan-target", "status": "scanned",
        "os_family": "openeuler", "os_version": "24.03", "package_manager": "rpm",
        "package_count": 300, "service_count": 1, "services": [{
            "protocol": "tcp", "listen": "0.0.0.0:22", "process": "sshd",
            "process_name": "sshd", "pid": "123",
            "executable": "/usr/sbin/sshd", "package_name": "openssh-server",
            "installed_version": "1.0-1.oe2403",
        }], "findings": [finding],
    }
    with patch("src.services.scan_engine.assess_asset", return_value=collected):
        response = client.post(f"/api/v1/scan-tasks/{task['id']}/start")

    assert response.status_code == 200
    assert response.json()["status"] == "scanning"
    completed = client.get(f"/api/v1/scan-tasks/{task['id']}").json()
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert [item["status"] for item in completed["execution_log"]] == [
        "queued",
        "scanning",
        "aggregating",
        "completed",
    ]
    assert completed["result_summary"]["assets_scanned"] == 1
    assert completed["result_summary"]["high"] == 1
    assert completed["result_summary"]["evidence_policy"] == "vendor_advisory_first"
    assert completed["findings"][0]["conclusion"] == "confirmed"
    report = client.get(f"/api/v1/scan-tasks/{task['id']}/report")
    assert report.status_code == 200
    assert "openEuler weekly assessment" in report.text
    assert "只读" in report.text
    assert "服务证据" in report.text
    assert "0.0.0.0:22" in report.text
    assert "openssh-server 1.0-1.oe2403" in report.text


def test_scan_assessment_uses_asset_credentials_without_host_key_registration(
    client: TestClient,
) -> None:
    asset_id = _create_asset(client, credential_id=_create_credential(client))
    task = _create_task(client, asset_id)
    collected = {
        "asset_id": asset_id, "asset_name": "scan-target", "status": "scanned",
        "os_family": "openeuler", "os_version": "24.03", "package_manager": "rpm",
        "package_count": 0, "service_count": 0, "services": [], "findings": [],
    }

    with patch(
        "src.services.scan_engine.assess_asset", return_value=collected
    ) as assess:
        response = client.post(f"/api/v1/scan-tasks/{task['id']}/start")

    assert response.status_code == 200
    assert len(assess.call_args.args) == 5


def test_scan_with_no_credential_fails_instead_of_reporting_zero_findings(
    client: TestClient,
) -> None:
    task = _create_task(client, _create_asset(client))

    response = client.post(f"/api/v1/scan-tasks/{task['id']}/start")
    failed = client.get(f"/api/v1/scan-tasks/{task['id']}").json()

    assert response.json()["status"] == "scanning"
    assert failed["status"] == "failed"
    assert failed["result_summary"]["assets_scanned"] == 0
    assert failed["result_summary"]["assets_failed"] == 1


def test_cancelled_task_can_be_rerun_without_overwriting_history(
    client: TestClient,
) -> None:
    original = _create_task(client, _create_asset(client))
    cancelled = client.post(
        f"/api/v1/scan-tasks/{original['id']}/cancel"
    ).json()

    rerun_response = client.post(
        f"/api/v1/scan-tasks/{original['id']}/rerun"
    )

    assert cancelled["status"] == "cancelled"
    assert rerun_response.status_code == 201
    rerun = rerun_response.json()
    assert rerun["id"] != original["id"]
    assert rerun["rerun_of_id"] == original["id"]
    assert rerun["status"] == "queued"
    assert client.get(f"/api/v1/scan-tasks/{original['id']}").json()["status"] == "cancelled"


def test_scan_task_rejects_missing_asset_and_illegal_transitions(
    client: TestClient,
) -> None:
    missing = client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "invalid scan",
            "scan_type": "host_vulnerability",
            "asset_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    task = _create_task(client, _create_asset(client))
    started = client.post(f"/api/v1/scan-tasks/{task['id']}/start")
    started_again = client.post(f"/api/v1/scan-tasks/{task['id']}/start")
    cancel_completed = client.post(f"/api/v1/scan-tasks/{task['id']}/cancel")

    assert missing.status_code == 400
    assert started.status_code == 200
    assert started_again.status_code == 409
    assert cancel_completed.status_code == 409
