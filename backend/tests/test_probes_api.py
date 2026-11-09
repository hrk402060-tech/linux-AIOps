from __future__ import annotations

from uuid import uuid4
import pytest


from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbChangeAudit, CmdbService, CmdbServiceAsset
from src.models.platform_setting import PlatformSetting


@pytest.fixture(autouse=True)
def approved_targets(client: TestClient) -> None:
    client.app.state.runtime_settings.probe_allowed_cidrs = "192.0.2.40/32"


def seed_target(session: Session) -> tuple[str, str]:
    asset = Asset(id=str(uuid4()), name="probe-host", host="192.0.2.40", os_family=OsFamily.UBUNTU, environment=AssetEnvironment.TEST)
    business = CmdbBusiness(id=str(uuid4()), code="PROBE", name="Probe")
    application = CmdbApplication(id=str(uuid4()), business_id=business.id, code="APP", name="App")
    service = CmdbService(id=str(uuid4()), application_id=application.id, code="API", name="API")
    session.add_all([asset, business, application, service, CmdbServiceAsset(id=str(uuid4()), service_id=service.id, asset_id=asset.id)])
    session.commit()
    return service.id, asset.id


def create_payload(service_id: str, asset_id: str, **values: object) -> dict[str, object]:
    return {"name": "Health check", "service_id": service_id, "asset_id": asset_id, "url": "http://192.0.2.40:8080/health", "method": "GET", "expected_status": 200, "timeout_seconds": 5, **values}


def test_probe_policy_is_visible_and_persisted_for_admin(client: TestClient) -> None:
    initial = client.get("/api/v1/probes/policy")
    assert initial.status_code == 200
    assert initial.json()["allowed_cidrs"] == ["192.0.2.40/32"]
    assert initial.json()["source"] == "环境变量"
    saved = client.put("/api/v1/probes/policy", json={"allowed_cidrs": "192.0.2.0/24\n198.51.100.10/32"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["allowed_cidrs"] == ["192.0.2.0/24", "198.51.100.10/32"]
    assert saved.json()["source"] == "平台配置"
    assert client.get("/api/v1/probes/policy").json()["source"] == "平台配置"


def test_probe_policy_rejects_invalid_network(client: TestClient) -> None:
    response = client.put("/api/v1/probes/policy", json={"allowed_cidrs": "not-a-network"})
    assert response.status_code == 400


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_only_admin_can_change_probe_policy(client: TestClient, role: str) -> None:
    from src.auth import AuthPrincipal, require_admin

    client.app.dependency_overrides[require_admin] = lambda: AuthPrincipal("policy-user", role, None, "session")
    try:
        assert client.get("/api/v1/probes/policy").status_code == 200
        assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": ""}).status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_probe_policy_requires_authentication(client: TestClient) -> None:
    client.headers.pop("Authorization")
    assert client.get("/api/v1/probes/policy").status_code == 401
    assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": ""}).status_code == 401


@pytest.mark.parametrize("cidr", [
    "not-a-network", "0.0.0.0/0", "::/0", "127.0.0.0/8",
    "169.254.0.0/16", "224.0.0.0/4", "::1/128", "::ffff:0:0/96", "fe80::/10",
])
def test_invalid_policy_does_not_replace_previous_policy(client: TestClient, cidr: str) -> None:
    assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": "192.0.2.0/24"}).status_code == 200
    rejected = client.put("/api/v1/probes/policy", json={"allowed_cidrs": cidr})
    assert rejected.status_code == 400
    assert client.get("/api/v1/probes/policy").json()["allowed_cidrs"] == ["192.0.2.0/24"]


def test_probe_policy_changes_are_audited_and_survive_new_sessions(
    client: TestClient, database_session: Session,
) -> None:
    for value in ["192.0.2.0/24\n192.0.2.0/24", ""]:
        assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": value}).status_code == 200
    audits = database_session.scalars(
        select(CmdbChangeAudit).where(CmdbChangeAudit.entity_type == "probe_policy")
        .order_by(CmdbChangeAudit.occurred_at, CmdbChangeAudit.id)
    ).all()
    assert len(audits) == 2
    assert audits[0].before_snapshot["allowed_cidrs"] == ["192.0.2.40/32"]
    assert audits[0].after_snapshot["allowed_cidrs"] == ["192.0.2.0/24"]
    assert audits[1].after_snapshot["allowed_cidrs"] == []
    assert all(item.operator_username == "break-glass-admin" and item.trace_id for item in audits)
    with client.app.state.session_factory() as reopened:
        setting = reopened.get(PlatformSetting, "probe_allowed_cidrs")
        assert setting is not None and setting.value == ""
    policy = client.get("/api/v1/probes/policy").json()
    assert policy["configured"] is False and policy["source"] == "平台配置"


def test_clearing_policy_revokes_existing_probe_runs(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import probes

    service_id, asset_id = seed_target(database_session)
    created = client.post("/api/v1/probes", json=create_payload(service_id, asset_id))
    assert created.status_code == 201
    assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": ""}).status_code == 200
    def transport(*args: object) -> dict[str, object]:
        pytest.fail("Revoked target must not be contacted")

    monkeypatch.setattr(probes, "check_http", transport)
    assert client.post(f"/api/v1/probes/{created.json()['id']}/run").status_code == 400
    assert client.post("/api/v1/probes", json=create_payload(service_id, asset_id)).status_code == 400


def test_policy_and_audit_rollback_together(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.probe_policy import save_policy

    save_policy(database_session, "192.0.2.0/24", "admin", "192.0.2.40/32")

    def fail_commit() -> None:
        raise SQLAlchemyError("simulated transaction failure")

    monkeypatch.setattr(database_session, "commit", fail_commit)
    with pytest.raises(SQLAlchemyError):
        save_policy(database_session, "", "admin", "")
    database_session.expire_all()
    assert database_session.get(PlatformSetting, "probe_allowed_cidrs").value == "192.0.2.0/24"
    assert len(database_session.scalars(select(CmdbChangeAudit)).all()) == 1


def test_approved_policy_cannot_bypass_platform_destinations(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import probe_http

    service_id, asset_id = seed_target(database_session)
    assert client.put("/api/v1/probes/policy", json={"allowed_cidrs": "192.0.2.0/24"}).status_code == 200
    monkeypatch.setattr(probe_http, "_platform_addresses", lambda: {"192.0.2.40"})
    response = client.post("/api/v1/probes", json=create_payload(service_id, asset_id))
    assert response.status_code == 400
    assert "management" in response.json()["detail"]


def test_probe_manual_run_retains_immutable_snapshot(client: TestClient, database_session: Session, monkeypatch) -> None:
    from src.services import probes
    service_id, asset_id = seed_target(database_session)
    monkeypatch.setattr(probes, "check_http", lambda *args: {"status": "healthy", "http_status": 200, "latency_ms": 8, "error_code": None})
    created = client.post("/api/v1/probes", json=create_payload(service_id, asset_id))
    assert created.status_code == 201, created.text
    probe_id = created.json()["id"]
    response = client.post(f"/api/v1/probes/{probe_id}/run")
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "healthy"
    assert run["operator_username"] == "break-glass-admin"
    assert run["service_id"] == service_id
    assert run["asset_id"] == asset_id
    assert run["trace_id"]
    assert client.get(f"/api/v1/probes/{probe_id}/runs").json()["total"] == 1
    assert client.delete(f"/api/v1/probes/{probe_id}").status_code == 405
    assert client.get("/api/v1/probes").json()["items"][0]["latest_run"]["id"] == run["id"]


def test_probe_rejects_unapproved_or_sensitive_destinations(client: TestClient, database_session: Session) -> None:
    service_id, asset_id = seed_target(database_session)
    for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://192.0.2.41/", "http://user:pass@192.0.2.40/", "http://192.0.2.40/?token=x", "file:///etc/passwd", "http://localhost/"):
        response = client.post("/api/v1/probes", json=create_payload(service_id, asset_id, url=url))
        assert response.status_code == 400, (url, response.text)
    assert client.post("/api/v1/probes", json=create_payload(service_id, asset_id, method="POST")).status_code == 422


def test_probe_disabled_asset_cannot_run(client: TestClient, database_session: Session, monkeypatch) -> None:
    from src.models.asset import AssetLifecycleStatus
    from src.services import probes
    service_id, asset_id = seed_target(database_session)
    probe_id = client.post("/api/v1/probes", json=create_payload(service_id, asset_id)).json()["id"]
    asset = database_session.get(Asset, asset_id)
    asset.lifecycle_status = AssetLifecycleStatus.DISABLED
    database_session.commit()
    def forbidden(*args: object) -> dict[str, object]:
        raise AssertionError("Disabled target must not be contacted")
    monkeypatch.setattr(probes, "check_http", forbidden)
    assert client.post(f"/api/v1/probes/{probe_id}/run").status_code == 400


def test_http_transport_does_not_follow_redirects_or_store_body(monkeypatch) -> None:
    from src.services import probe_http
    class Response:
        status = 302
    class Connection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            assert kwargs["timeout"] == 3
        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "HEAD"
            assert path == "/health"
        def connect(self) -> None:
            return None
        sock = None
        def getresponse(self) -> Response:
            return Response()
        def close(self) -> None:
            return None
    monkeypatch.setattr(probe_http.http.client, "HTTPConnection", Connection)
    result = probe_http.check_http("http://192.0.2.40/health", "HEAD", 200, 3)
    assert result["status"] == "unhealthy"
    assert result["http_status"] == 302
    assert "body" not in result
