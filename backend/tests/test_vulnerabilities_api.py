from unittest.mock import Mock, patch

import pytest

from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _registered_host_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.services.scan_tasks.ssh_host_key_registry.ensure_registered",
        lambda *_: Mock(),
    )


def _create_completed_scan(client: TestClient) -> dict[str, object]:
    credential = client.post(
        "/api/v1/credentials",
        json={
            "name": "finding-reader",
            "credential_type": "password",
            "username": "audit",
            "secret": "server-only-secret",
        },
    ).json()
    asset = client.post(
        "/api/v1/assets",
        json={
            "name": "openEuler gateway",
            "host": "192.0.2.20",
            "ssh_port": 22,
            "os_family": "openeuler",
            "environment": "production",
            "group_name": "edge",
            "credential_id": credential["id"],
            "labels": ["internet-facing"],
        },
    ).json()
    task = client.post(
        "/api/v1/scan-tasks",
        json={
            "name": "gateway assessment",
            "scan_type": "comprehensive",
            "asset_ids": [asset["id"]],
        },
    ).json()
    findings = [
        {
            "cve_id": "CVE-2026-1001",
            "advisory_id": "openEuler-SA-2026-1001",
            "title": "Kernel privilege boundary issue",
            "severity": "critical",
            "package_name": "kernel",
            "installed_version": "1.0-1",
            "fixed_version": "1.0-2",
            "source_name": "openEuler Security Advisories",
            "vendor_url": "https://example.test/openEuler-SA-2026-1001",
            "conclusion": "confirmed",
            "confidence": "high",
            "evidence_chain": [
                {"type": "source", "value": "openEuler Security Advisories"},
                {"type": "advisory", "value": "openEuler-SA-2026-1001"},
                {"type": "installed_version", "value": "1.0-1"},
                {"type": "fixed_version", "value": "1.0-2"},
                {"type": "conclusion", "value": "confirmed"},
            ],
        },
        {
            "cve_id": "CVE-2026-1002",
            "advisory_id": "CNVD-2026-1002",
            "title": "OpenSSL compatibility issue",
            "severity": "high",
            "package_name": "openssl",
            "installed_version": "3.0-1",
            "fixed_version": "3.0-2",
            "source_name": "CNVD",
            "vendor_url": None,
            "conclusion": "manual_confirmation",
            "confidence": "low",
            "evidence_chain": [
                {"type": "source", "value": "CNVD"},
                {"type": "advisory", "value": "CNVD-2026-1002"},
                {"type": "installed_version", "value": "3.0-1"},
                {"type": "fixed_version", "value": "3.0-2"},
                {"type": "conclusion", "value": "manual_confirmation"},
            ],
        },
    ]
    collected = {
        "asset_id": asset["id"],
        "asset_name": asset["name"],
        "status": "scanned",
        "os_family": "openeuler",
        "os_version": "24.03",
        "package_manager": "rpm",
        "package_count": 300,
        "service_count": 4,
        "services": [],
        "findings": findings,
    }
    with patch("src.services.scan_engine.assess_asset", return_value=collected):
        response = client.post(f"/api/v1/scan-tasks/{task['id']}/start")
    assert response.status_code == 200
    return task


def test_vulnerabilities_list_exposes_summary_and_filters(client: TestClient) -> None:
    task = _create_completed_scan(client)

    response = client.get("/api/v1/vulnerabilities")
    filtered = client.get(
        "/api/v1/vulnerabilities",
        params={"severity": "critical", "conclusion": "confirmed"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["summary"] == {
        "critical": 1,
        "high": 1,
        "medium": 0,
        "low": 0,
        "confirmed": 1,
        "manual_confirmation": 1,
    }
    assert body["items"][0]["scan_task_id"] == task["id"]
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["cve_id"] == "CVE-2026-1001"


def test_vulnerability_detail_preserves_evidence_and_snapshot(client: TestClient) -> None:
    _create_completed_scan(client)
    finding = client.get(
        "/api/v1/vulnerabilities", params={"query": "openssl"}
    ).json()["items"][0]

    detail = client.get(f"/api/v1/vulnerabilities/{finding['id']}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["asset_name"] == "openEuler gateway"
    assert body["knowledge_version"] is None
    assert body["conclusion"] == "manual_confirmation"
    assert body["confidence"] == "low"
    assert [item["type"] for item in body["evidence_chain"]] == [
        "source",
        "advisory",
        "installed_version",
        "fixed_version",
        "conclusion",
    ]
    assert client.get("/api/v1/vulnerabilities/not-found").status_code == 404
