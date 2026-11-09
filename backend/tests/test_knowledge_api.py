import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from src.services import knowledge, knowledge_sync


def _package(version: str, cve_id: str = "CVE-2026-1001") -> bytes:
    payload = {
        "schema_version": "1.0",
        "package_id": f"pkg-{version}",
        "version": version,
        "name": f"openEuler 安全公告 {version}",
        "source_name": "openEuler Security Advisories",
        "published_at": "2026-09-03T08:00:00Z",
        "advisories": [
            {
                "cve_id": cve_id,
                "advisory_id": f"openEuler-SA-{version}",
                "title": "内核权限提升漏洞",
                "severity": "high",
                "os_family": "openeuler",
                "vendor_url": "https://www.openeuler.org/zh/security/security-bulletins/",
                "affected_packages": [
                    {
                        "name": "kernel",
                        "affected_version": "< 6.6.0-80.0.0.95",
                        "fixed_version": "6.6.0-80.0.0.95.oe2403",
                        "service_banner_keywords": ["Linux kernel"],
                    }
                ],
                "published_at": "2026-09-01T08:00:00Z",
                "updated_at": "2026-09-03T08:00:00Z",
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _signing_material() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, base64.b64encode(public_key).decode()


def _import_package(
    client: TestClient, private_key: Ed25519PrivateKey, content: bytes, key_id: str = "test-key"
):
    return client.post(
        "/api/v1/knowledge/imports",
        json={
            "filename": "openeuler-package.json",
            "package_content": base64.b64encode(content).decode(),
            "signature": base64.b64encode(private_key.sign(content)).decode(),
            "key_id": key_id,
        },
    )


def test_import_signed_package_creates_active_release(client: TestClient) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}

    response = _import_package(client, private_key, _package("2026.09.03"))

    assert response.status_code == 201
    assert response.json()["status"] == "active"
    summary = client.get("/api/v1/knowledge/summary").json()
    assert summary["active_version"] == "2026.09.03"
    assert summary["advisory_count"] == 1
    advisory = client.get("/api/v1/knowledge/advisories").json()[0]
    assert advisory["source_name"] == "openEuler Security Advisories"
    assert advisory["affected_packages"][0]["fixed_version"].endswith("oe2403")
    assert advisory["affected_packages"][0]["service_banner_keywords"] == ["Linux kernel"]


def test_invalid_signature_is_rejected_without_release(client: TestClient) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}
    content = _package("2026.09.04")
    signature = private_key.sign(content)

    response = client.post(
        "/api/v1/knowledge/imports",
        json={
            "filename": "tampered.json",
            "package_content": base64.b64encode(content + b" ").decode(),
            "signature": base64.b64encode(signature).decode(),
            "key_id": "test-key",
        },
    )

    assert response.status_code == 422
    assert client.get("/api/v1/knowledge/releases").json() == []
    runs = client.get("/api/v1/knowledge/sync-runs").json()
    assert runs[0]["status"] == "failed"
    assert "签名" in runs[0]["message"]


def test_coverage_lists_every_source_and_discloses_incomplete_data(client: TestClient) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}
    _import_package(client, private_key, _package("2026.09.03-demo"))

    response = client.get("/api/v1/knowledge/coverage")

    assert response.status_code == 200
    body = response.json()
    assert body["is_complete"] is False
    assert body["publication_policy"] == "stage_validate_publish"
    assert body["failure_policy"] == "retain_active_release_and_alert"
    assert body["critical_check_interval_hours"] == 4
    assert len(body["sources"]) == 9
    by_name = {item["source_name"]: item for item in body["sources"]}
    assert by_name["openEuler Security Advisories"]["advisory_count"] == 1
    assert by_name["openEuler Security Advisories"]["latest_advisory_at"] is not None
    assert by_name["openEuler Security Advisories"]["last_success_at"] is not None
    assert by_name["openEuler Security Advisories"]["daily_schedule"] == "0 2 * * *"
    assert by_name["CNVD"]["sync_mode"] == "offline_package"
    assert by_name["CNVD"]["health"] == "never_synced"


def test_failed_update_retains_active_release_and_raises_coverage_alert(
    client: TestClient,
) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}
    active = _import_package(client, private_key, _package("2026.09.03")).json()
    invalid_payload = json.loads(_package("2026.09.04"))
    invalid_payload["advisories"] = []
    content = json.dumps(invalid_payload, ensure_ascii=False, separators=(",", ":")).encode()

    failed = client.post(
        "/api/v1/knowledge/imports",
        json={
            "filename": "invalid-update.json",
            "package_content": base64.b64encode(content).decode(),
            "signature": base64.b64encode(private_key.sign(content)).decode(),
            "key_id": "test-key",
        },
    )

    assert failed.status_code == 422
    releases = client.get("/api/v1/knowledge/releases").json()
    assert next(item for item in releases if item["id"] == active["id"])["status"] == "active"
    coverage = client.get("/api/v1/knowledge/coverage").json()
    open_euler = next(
        item for item in coverage["sources"]
        if item["source_name"] == "openEuler Security Advisories"
    )
    assert open_euler["health"] == "failed"
    assert open_euler["has_alert"] is True


def test_release_can_be_rolled_back(client: TestClient) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}
    first = _import_package(client, private_key, _package("2026.09.01", "CVE-2026-1001")).json()
    second = _import_package(client, private_key, _package("2026.09.02", "CVE-2026-1002")).json()
    assert second["status"] == "active"

    response = client.post(f"/api/v1/knowledge/releases/{first['id']}/rollback")

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    advisories = client.get("/api/v1/knowledge/advisories").json()
    assert [item["cve_id"] for item in advisories] == ["CVE-2026-1001"]


def test_import_from_another_source_keeps_existing_advisories(client: TestClient) -> None:
    private_key, public_key = _signing_material()
    client.app.state.knowledge_trusted_keys = {"test-key": public_key}
    _import_package(client, private_key, _package("2026.09.01", "CVE-2026-1001"))
    content = json.loads(_package("2026.09.02", "CVE-2026-1002"))
    content["source_name"] = "Kylin Security Advisories"
    content["advisories"][0]["os_family"] = "kylin"
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()

    response = _import_package(client, private_key, encoded)

    assert response.status_code == 201
    assert response.json()["advisory_count"] == 2
    advisories = client.get("/api/v1/knowledge/advisories").json()
    assert {item["cve_id"] for item in advisories} == {"CVE-2026-1001", "CVE-2026-1002"}


def test_domestic_sources_are_offline_first(client: TestClient) -> None:
    sources = client.get("/api/v1/knowledge/sources").json()
    domestic = {item["name"]: item for item in sources if item["name"] in {"CNVD", "CNNVD"}}

    assert domestic["CNVD"]["sync_mode"] == "offline_package"
    assert domestic["CNNVD"]["sync_mode"] == "offline_package"
    response = client.post(f"/api/v1/knowledge/sources/{domestic['CNVD']['id']}/sync")
    assert response.status_code == 409


def test_online_connector_download_enforces_signature_and_source(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key, public_key = _signing_material()
    content = _package("2026.09.05")
    signature = private_key.sign(content)
    connector = {
        "package_url": "https://security.example.test/openeuler.json",
        "signature_url": "https://security.example.test/openeuler.sig",
        "key_id": "online-test-key",
    }

    def download(url: str) -> bytes:
        return signature if url.endswith(".sig") else content

    monkeypatch.setattr(knowledge_sync, "_download", download)
    release = knowledge_sync.sync_source(
        database_session,
        "openEuler Security Advisories",
        connector,
        {"online-test-key": public_key},
    )
    assert release.version == "2026.09.05"
    assert knowledge.list_sync_runs(database_session)[0].mode == "online"

    with pytest.raises(knowledge.KnowledgeImportError, match="来源.*不匹配"):
        knowledge_sync.sync_source(
            database_session,
            "Debian Security Advisories",
            connector,
            {"online-test-key": public_key},
        )

    monkeypatch.setattr(knowledge_sync, "_download", lambda url: b"invalid" if url.endswith(".sig") else content)
    with pytest.raises(knowledge.KnowledgeImportError, match="签名校验失败"):
        knowledge_sync.sync_source(
            database_session,
            "openEuler Security Advisories",
            connector,
            {"online-test-key": public_key},
        )

    assert len(knowledge.list_releases(database_session)) == 1
