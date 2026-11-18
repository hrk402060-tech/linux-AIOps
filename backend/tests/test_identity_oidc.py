from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

from src.models.platform_setting import PlatformSetting
from src.services import identity_flows

BASE = "/api/v1/system/identity-sources/oidc"
OIDC = {
    "issuer_url": "https://identity.example.test/realm/ops",
    "client_id": "linux-ops",
    "client_secret": "oidc-client-secret-for-test",
    "redirect_uri": "http://192.168.10.17:8088/api/v1/auth/identity/oidc/callback",
    "scopes": ["openid", "profile", "email"],
    "timeout_seconds": 10,
}
METADATA = {
    "issuer": OIDC["issuer_url"],
    "authorization_endpoint": "https://identity.example.test/authorize",
    "token_endpoint": "https://identity.example.test/token",
    "jwks_uri": "https://identity.example.test/keys",
}


def prepare_oidc(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.services.identity_oidc.discover", lambda _: METADATA)
    monkeypatch.setattr("src.services.identity_oidc.test_connection", lambda _: "OIDC 可用")
    assert client.put(BASE, json=OIDC).status_code == 200
    assert client.post(f"{BASE}/test", json=OIDC).json()["success"] is True
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(key="settings.authentication", value=json.dumps({
            "local_login_enabled": True, "ldap_enabled": False,
            "oidc_enabled": True, "new_account_policy": "admin_only",
        })))
        session.commit()


def test_oidc_start_uses_state_nonce_pkce_and_http_only_browser_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_oidc(client, monkeypatch)
    response = client.get("/api/v1/auth/identity/oidc/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["linux-ops"]
    assert len(query["state"][0]) >= 32
    assert len(query["nonce"][0]) >= 32
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert "HttpOnly" in response.headers["set-cookie"]


def test_oidc_callback_rejects_mismatched_browser_and_replay(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_oidc(client, monkeypatch)
    started = client.get("/api/v1/auth/identity/oidc/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    browser_token = client.cookies.get("identity_oidc_flow")
    assert browser_token is not None
    client.cookies.clear()
    rejected = client.get(
        f"/api/v1/auth/identity/oidc/callback?state={state}&code=fake",
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    client.cookies.set("identity_oidc_flow", browser_token)
    denied = client.get(
        f"/api/v1/auth/identity/oidc/callback?state={state}&error=access_denied",
        follow_redirects=False,
    )
    assert denied.status_code == 400
    client.cookies.set("identity_oidc_flow", browser_token)
    replay = client.get(
        f"/api/v1/auth/identity/oidc/callback?state={state}&code=fake",
        follow_redirects=False,
    )
    assert replay.status_code == 400


def test_encrypted_flow_is_single_use_and_checks_revision(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        vault = client.app.state.credential_vault
        token = identity_flows.create(session, vault, "test", {
            "browser_hash": "bound-browser", "revision": "revision-1",
        })
        with pytest.raises(ValueError):
            identity_flows.consume(session, vault, "test", token, "wrong-browser")
        flow = identity_flows.consume(session, vault, "test", token, "bound-browser")
        assert flow["revision"] == "revision-1"
        with pytest.raises(ValueError):
            identity_flows.consume(session, vault, "test", token, "bound-browser")


def mapped_oidc_user(client: TestClient, subject: str) -> str:
    user = client.post("/api/v1/auth/users", json={
        "username": "oidc-auditor", "password": "Local-Unrelated-2026",
        "role": "auditor",
    })
    assert user.status_code == 201, user.text
    user_id = user.json()["id"]
    assert client.post(f"{BASE}/bindings", json={
        "subject": subject, "user_id": user_id,
    }).status_code == 201
    return str(user_id)


def test_verified_oidc_callback_issues_only_the_mapped_platform_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_oidc(client, monkeypatch)
    subject = "immutable-oidc-subject"
    user_id = mapped_oidc_user(client, subject)
    started = client.get("/api/v1/auth/identity/oidc/start", follow_redirects=False)
    query = parse_qs(urlparse(started.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    captured: dict[str, str] = {}

    def exchange(
        configuration: dict[str, object], metadata: dict[str, object],
        code: str, verifier: str, returned_nonce: str,
    ) -> dict[str, str]:
        captured.update(code=code, verifier=verifier, nonce=returned_nonce)
        assert configuration["client_id"] == OIDC["client_id"]
        assert metadata["issuer"] == OIDC["issuer_url"]
        return {"subject": subject}

    monkeypatch.setattr("src.services.identity_oidc.exchange", exchange)
    result = client.get(
        "/api/v1/auth/identity/oidc/callback",
        params={"state": state, "code": "provider-authorization-code"},
        follow_redirects=False,
    )
    assert result.status_code == 302, result.text
    assert result.headers["location"] == "/"
    assert captured["code"] == "provider-authorization-code"
    assert captured["nonce"] == nonce
    assert len(captured["verifier"]) >= 43
    assert client.app.state.runtime_settings.auth_cookie_name in result.headers["set-cookie"]
    client.headers.pop("Authorization")
    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200, current.text
    assert current.json()["id"] == user_id
    assert current.json()["role"] == "auditor"
    assert current.json()["asset_scope"] == "assigned"
    assert client.get(
        "/api/v1/auth/identity/oidc/callback",
        params={"state": state, "code": "provider-authorization-code"},
        follow_redirects=False,
    ).status_code == 400


@pytest.mark.parametrize("overrides", [
    {"issuer_url": "http://identity.example.test"},
    {"redirect_uri": "https://ops.example.test/evil?return_to=external"},
    {"scopes": ["profile"]},
])
def test_invalid_oidc_configuration_is_rejected_without_echoing_secret(
    client: TestClient, overrides: dict[str, object],
) -> None:
    result = client.put(BASE, json={**OIDC, **overrides})
    assert result.status_code in {400, 422}
    assert OIDC["client_secret"] not in result.text
