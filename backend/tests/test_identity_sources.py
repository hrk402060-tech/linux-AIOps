from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services import identity_sources

BASE = "/api/v1/system/identity-sources"
LDAP_SECRET = "directory-bind-secret-for-test"
LDAP = {
    "server_url": "ldaps://directory.example.test:636",
    "base_dn": "dc=example,dc=test",
    "bind_dn": "cn=reader,dc=example,dc=test",
    "bind_password": LDAP_SECRET,
    "user_filter": "(uid={username})",
    "username_attribute": "uid",
    "subject_attribute": "entryUUID",
    "display_name_attribute": "displayName",
    "timeout_seconds": 10,
}


def test_source_secret_is_encrypted_and_never_in_response_or_audit(
    client: TestClient,
) -> None:
    response = client.put(f"{BASE}/ldap", json=LDAP)
    assert response.status_code == 200, response.text
    assert response.json()["secret_configured"] is True
    assert LDAP_SECRET not in response.text
    assert "bind_password" not in response.json()
    with client.app.state.session_factory() as session:
        item = session.get(PlatformSetting, "identity.source.ldap")
        assert item is not None
        assert LDAP_SECRET not in item.value
        snapshot = json.loads(item.value)
        assert snapshot["secret"]["key_version"] == "test-v1"
        audit = session.scalar(select(CmdbChangeAudit).where(
            CmdbChangeAudit.entity_type == "identity_source",
        ))
        assert audit is not None
        assert LDAP_SECRET not in json.dumps(audit.after_snapshot)
    assert client.get(f"{BASE}/ldap").json()["server_url"] == LDAP["server_url"]


def test_empty_password_preserves_stored_secret(client: TestClient) -> None:
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 200
    saved = client.put(f"{BASE}/ldap", json={**LDAP, "bind_password": ""})
    assert saved.status_code == 200
    with client.app.state.session_factory() as session:
        configuration = identity_sources.load_configuration(
            session, client.app.state.credential_vault, "ldap",
        )
        assert configuration["bind_password"] == LDAP_SECRET


def test_invalid_source_update_keeps_previous_value_and_hides_input(
    client: TestClient,
) -> None:
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 200
    rejected = client.put(f"{BASE}/ldap", json={
        **LDAP, "server_url": "ldap://unsafe.example.test",
        "start_tls": False,
    })
    assert rejected.status_code in {400, 422}
    assert LDAP_SECRET not in rejected.text
    assert client.get(f"{BASE}/ldap").json()["server_url"] == LDAP["server_url"]


def test_connection_test_uses_draft_credentials_and_returns_actual_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, object]] = []

    def probe(configuration: dict[str, object]) -> str:
        seen.append(configuration)
        raise identity_sources.IdentitySourceError("目录服务器连接失败或 TLS 校验未通过")

    monkeypatch.setattr("src.services.identity_ldap.test_connection", probe)
    result = client.post(f"{BASE}/ldap/test", json=LDAP)
    assert result.status_code == 200
    assert result.json()["success"] is False
    assert "TLS" in result.json()["message"]
    assert seen[0]["bind_password"] == LDAP_SECRET
    assert LDAP_SECRET not in result.text


def test_test_status_only_applies_to_exact_saved_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.services.identity_ldap.test_connection", lambda _: "已连接目录")
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 200
    draft = client.post(f"{BASE}/ldap/test", json={**LDAP, "base_dn": "dc=other"})
    assert draft.json()["success"] is True
    assert client.get(f"{BASE}/ldap").json()["test_success"] is False
    result = client.post(f"{BASE}/ldap/test", json=LDAP)
    assert result.json()["success"] is True
    saved = client.get(f"{BASE}/ldap").json()
    assert saved["test_success"] is True
    assert saved["tested_at"] is not None


def test_disabled_provider_and_unknown_provider_cannot_authenticate(client: TestClient) -> None:
    result = client.post("/api/v1/auth/identity/ldap/login", json={
        "username": "someone", "password": "external-password",
    })
    assert result.status_code == 409
    assert client.get(f"{BASE}/unsupported").status_code in {404, 422}


def test_only_admin_can_read_save_and_test_sources(client: TestClient) -> None:
    created = client.post("/api/v1/auth/users", json={
        "username": "identity-operator", "password": "Operator-Test-2026",
        "role": "operator",
    })
    assert created.status_code == 201
    client.headers.pop("Authorization")
    assert client.post("/api/v1/auth/login", json={
        "username": "identity-operator", "password": "Operator-Test-2026",
    }).status_code == 200
    assert client.get(f"{BASE}/ldap").status_code == 403
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 403
    assert client.post(f"{BASE}/ldap/test", json=LDAP).status_code == 403


def test_cannot_disable_every_login_method(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        with pytest.raises(ValueError, match="至少保留"):
            identity_sources.validate_authentication_settings(session, {
                "local_login_enabled": False, "ldap_enabled": False,
                "oidc_enabled": False, "new_account_policy": "admin_only",
            })


def test_untested_external_source_cannot_replace_local_login(client: TestClient) -> None:
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 200
    with client.app.state.session_factory() as session:
        with pytest.raises(ValueError, match="测试"):
            identity_sources.validate_authentication_settings(session, {
                "local_login_enabled": False, "ldap_enabled": True,
                "oidc_enabled": False, "new_account_policy": "admin_only",
            })


def test_invalid_login_does_not_echo_password(client: TestClient) -> None:
    secret = "private-directory-password"
    response = client.post("/api/v1/auth/identity/ldap/login", json={
        "username": 42, "password": secret,
    })
    assert response.status_code == 422
    assert secret not in response.text


def test_source_response_is_not_cacheable(client: TestClient) -> None:
    response = client.get(f"{BASE}/ldap")
    assert response.headers["cache-control"] == "no-store"


def test_concurrent_administrator_save_preserves_latest_configuration(
    client: TestClient,
) -> None:
    saved = client.put(f"{BASE}/ldap", json=LDAP)
    assert saved.status_code == 200
    first_revision = saved.json()["revision"]
    assert client.put(f"{BASE}/ldap", json={
        **LDAP, "display_name_attribute": "cn",
    }, headers={"If-Match": first_revision}).status_code == 200
    rejected = client.put(f"{BASE}/ldap", json={
        **LDAP, "display_name_attribute": "name",
    }, headers={"If-Match": first_revision})
    assert rejected.status_code == 409
    assert client.get(f"{BASE}/ldap").json()["display_name_attribute"] == "cn"
