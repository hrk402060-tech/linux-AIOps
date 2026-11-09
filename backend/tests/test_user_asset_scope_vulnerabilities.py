from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.vulnerability import VulnerabilityCase, VulnerabilityFinding


ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


def _create_user(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/users", headers=ADMIN, json={
        "username": "vulnerability-scoped-user",
        "password": "Scope-Test-2026!",
        "role": "operator",
    })
    assert response.status_code == 201, response.text
    return response.json()


def _create_asset(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/assets", headers=ADMIN, json={
        "name": name,
        "host": "10.0.0.31",
        "ssh_username": "ops",
        "ssh_secret": "test-ssh-secret",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _grant_asset(client: TestClient, user: dict[str, str], asset_id: str) -> None:
    current = client.get(
        f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN
    )
    assert current.status_code == 200
    response = client.put(
        f"/api/v1/auth/users/{user['id']}/asset-access",
        headers=ADMIN,
        json={
            "asset_scope": "assigned",
            "expected_revision": current.json()["revision"],
            "entries": [{"asset_id": asset_id, "can_ssh": False}],
            "confirmation": user["username"],
            "reason": "漏洞范围隔离测试",
        },
    )
    assert response.status_code == 200, response.text


def _add_finding(session: Session, asset_id: str, suffix: str) -> str:
    case_id = f"case-{suffix}"
    finding_id = f"finding-{suffix}"
    session.add(VulnerabilityCase(
        id=case_id,
        asset_id=asset_id,
        asset_name=f"node-{suffix}",
        cve_id=f"CVE-2026-{suffix}",
        status="open",
        audit_history=[],
    ))
    session.add(VulnerabilityFinding(
        id=finding_id,
        case_id=case_id,
        scan_task_id=f"scan-{suffix}",
        scan_task_name="scope-test",
        asset_id=asset_id,
        asset_name=f"node-{suffix}",
        knowledge_version="kb-test",
        cve_id=f"CVE-2026-{suffix}",
        advisory_id=f"ADV-{suffix}",
        title="范围隔离测试漏洞",
        severity="high",
        package_name="openssl",
        installed_version="1.0",
        fixed_version="2.0",
        source_name="厂商公告",
        vendor_url=None,
        conclusion="confirmed",
        confidence="high",
        evidence_chain=[{"type": "source", "value": "vendor"}],
        detected_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return finding_id


def _sign_in(client: TestClient, user: dict[str, str]) -> None:
    client.headers.pop("authorization", None)
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={
        "username": user["username"],
        "password": "Scope-Test-2026!",
    })
    assert response.status_code == 200, response.text


def test_scoped_user_cannot_read_or_approve_hidden_vulnerability(
    client: TestClient, database_session: Session
) -> None:
    user = _create_user(client)
    allowed_asset = _create_asset(client, "vulnerability-allowed")
    hidden_asset = _create_asset(client, "vulnerability-hidden")
    allowed_finding = _add_finding(database_session, allowed_asset, "1001")
    hidden_finding = _add_finding(database_session, hidden_asset, "1002")
    _grant_asset(client, user, allowed_asset)
    _sign_in(client, user)

    listing = client.get("/api/v1/vulnerabilities")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [allowed_finding]
    assert body["summary"]["high"] == 1

    filtered = client.get(
        f"/api/v1/vulnerabilities?asset_id={hidden_asset}"
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0

    assert client.get(
        f"/api/v1/vulnerabilities/{hidden_finding}"
    ).status_code == 403
    disposition = client.patch(
        f"/api/v1/vulnerabilities/{hidden_finding}/disposition",
        json={"status": "ignored", "reason": "范围测试"},
    )
    assert disposition.status_code == 403

    patches = client.get("/api/v1/patches")
    assert patches.status_code == 200
    assert [item["finding_id"] for item in patches.json()] == [allowed_finding]
    approval = client.post(
        f"/api/v1/patches/{hidden_finding}/approve",
        json={
            "reason": "范围测试",
            "confirmation": f"APPROVE {hidden_finding}",
        },
    )
    assert approval.status_code == 403
