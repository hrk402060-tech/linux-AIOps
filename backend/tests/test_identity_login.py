from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.auth import AuthSession, User
from src.models.platform_setting import PlatformSetting
from src.services import identity_config, identity_sources, mfa
from test_identity_sources import BASE, LDAP

LOGIN = "/api/v1/auth/identity/ldap/login"
EXTERNAL_SUBJECT = "directory-immutable-user-id"


def prepare_directory(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("src.services.identity_ldap.test_connection", lambda _: "目录可用")
    monkeypatch.setattr(
        "src.services.identity_ldap.authenticate",
        lambda *_: {"subject": EXTERNAL_SUBJECT, "display_name": "Directory User"},
    )
    assert client.put(f"{BASE}/ldap", json=LDAP).status_code == 200
    assert client.post(f"{BASE}/ldap/test", json=LDAP).json()["success"] is True
    user = client.post("/api/v1/auth/users", json={
        "username": "directory-user", "password": "Local-Unrelated-2026",
        "role": "operator", "asset_scope": "assigned",
    })
    assert user.status_code == 201
    user_id: str = user.json()["id"]
    linked = client.post(f"{BASE}/ldap/bindings", json={
        "subject": EXTERNAL_SUBJECT, "user_id": user_id,
    })
    assert linked.status_code == 201, linked.text
    with client.app.state.session_factory() as session:
        item = session.get(PlatformSetting, "settings.authentication")
        if item is None:
            item = PlatformSetting(key="settings.authentication")
            session.add(item)
        item.value = json.dumps({
            "local_login_enabled": True, "ldap_enabled": True,
            "oidc_enabled": False, "new_account_policy": "admin_only",
        })
        session.commit()
    return user_id


def test_ldap_login_uses_existing_session_and_asset_permissions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    result = client.post(LOGIN, json={"username": "remote", "password": "Directory-Pass"})
    assert result.status_code == 200, result.text
    assert result.json()["id"] == user_id
    assert result.json()["asset_scope"] == "assigned"
    assert "HttpOnly" in result.headers["set-cookie"]
    client.headers.pop("Authorization")
    assert client.get("/api/v1/auth/me").json()["id"] == user_id


def test_external_username_does_not_auto_link_local_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_directory(client, monkeypatch)
    monkeypatch.setattr(
        "src.services.identity_ldap.authenticate",
        lambda *_: {"subject": "unlinked-subject", "username": "directory-user"},
    )
    rejected = client.post(LOGIN, json={
        "username": "directory-user", "password": "Directory-Pass",
    })
    assert rejected.status_code == 403
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id)) is None


def test_disabled_and_locked_users_cannot_login_via_directory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.enabled = False
        session.commit()
    assert client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    }).status_code == 401


def test_ldap_reuses_mfa_and_does_not_issue_session_before_verification(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    setup = client.post(f"/api/v1/auth/users/{user_id}/mfa/setup").json()
    code = mfa._totp(setup["secret"], int(time.time()) // 30)
    assert client.post(
        f"/api/v1/auth/users/{user_id}/mfa/confirm", json={"code": code},
    ).status_code == 200
    response = client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "mfa_required"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id)) is None
    verified = client.post("/api/v1/auth/identity/mfa/verify", json={"code": code})
    assert verified.status_code == 200, verified.text
    assert verified.json()["id"] == user_id
    assert client.post("/api/v1/auth/identity/mfa/verify", json={"code": code}).status_code == 401


def test_last_login_guard_requires_verified_enabled_administrator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    proposed = {
        "local_login_enabled": False, "ldap_enabled": True,
        "oidc_enabled": False, "new_account_policy": "admin_only",
    }
    with client.app.state.session_factory() as session:
        with pytest.raises(ValueError, match="管理员"):
            identity_sources.validate_authentication_settings(session, proposed)
        user = session.get(User, user_id)
        assert user is not None
        user.role = "admin"
        session.commit()
    assert client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    }).status_code == 200
    with client.app.state.session_factory() as session:
        identity_sources.validate_authentication_settings(session, proposed)


def test_forced_mfa_enrollment_after_ldap_login_issues_no_early_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    assert client.put("/api/v1/system/auth-policy", json={
        "login_failure_threshold": 5, "lock_duration_seconds": 900,
        "session_ttl_seconds": 43200, "require_mfa": True,
        "mfa_enforcement": "all", "allow_user_mfa_reset": True,
    }).status_code == 200
    result = client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    })
    assert result.status_code == 401
    assert result.json()["detail"] == "mfa_setup_required"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id)) is None
    status = client.get("/api/v1/auth/identity/mfa/status").json()
    assert status == {"required": True, "enrollment_required": True}
    setup = client.post("/api/v1/auth/identity/mfa/setup")
    assert setup.status_code == 200, setup.text
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    confirmed = client.post("/api/v1/auth/identity/mfa/confirm", json={"code": code})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user"]["id"] == user_id
    assert len(confirmed.json()["recovery_codes"]) == 8
    client.headers.pop("Authorization")
    assert client.get("/api/v1/auth/me").json()["id"] == user_id


def test_source_revision_change_invalidates_pending_mfa_challenge(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    setup = client.post(f"/api/v1/auth/users/{user_id}/mfa/setup").json()
    code = mfa._totp(setup["secret"], int(time.time()) // 30)
    assert client.post(
        f"/api/v1/auth/users/{user_id}/mfa/confirm", json={"code": code},
    ).status_code == 200
    assert client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    }).status_code == 401
    updated = client.put(f"{BASE}/ldap", json={
        **LDAP, "display_name_attribute": "cn",
    })
    assert updated.status_code == 200, updated.text
    assert client.post(
        "/api/v1/auth/identity/mfa/verify", json={"code": code},
    ).status_code == 401
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id)) is None


def test_last_external_admin_cannot_be_disabled_even_with_unmapped_local_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    assert client.post("/api/v1/auth/users", json={
        "username": "unmapped-local-admin", "password": "Local-Admin-Test-2026",
        "role": "admin",
    }).status_code == 201
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.role = "admin"
        session.commit()
    assert client.post(LOGIN, json={
        "username": "remote", "password": "Directory-Pass",
    }).status_code == 200
    with client.app.state.session_factory() as session:
        item = session.get(PlatformSetting, "settings.authentication")
        assert item is not None
        item.value = json.dumps({
            "local_login_enabled": False, "ldap_enabled": True,
            "oidc_enabled": False, "new_account_policy": "admin_only",
        })
        session.commit()
        with pytest.raises(ValueError, match="最后一个"):
            identity_config.ensure_external_administrator_remains(
                session, user_id, {"enabled": False},
            )
        with pytest.raises(ValueError, match="最后一个"):
            identity_config.ensure_external_administrator_remains(
                session, user_id, {"role": "operator"},
            )
    for changes in ({"enabled": False}, {"role": "operator"}):
        rejected = client.patch(f"/api/v1/auth/users/{user_id}", json=changes)
        assert rejected.status_code == 409, rejected.text
        assert "最后一个" in rejected.json()["detail"]
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.enabled is True
        assert user.role == "admin"
