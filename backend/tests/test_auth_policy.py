from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.models.auth import AuthSession
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services import mfa
from src.services.auth import hash_token

PASSWORD = "Policy-Password-2026"


def policy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "login_failure_threshold": 5,
        "lock_duration_seconds": 900,
        "session_ttl_seconds": 43200,
        "require_mfa": False,
    }
    payload.update(overrides)
    return payload


def create_policy_user(
    client: TestClient, username: str, role: str = "operator",
) -> str:
    response = client.post("/api/v1/auth/users", json={
        "username": username, "password": PASSWORD, "role": role,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def enable_user_mfa(client: TestClient, user_id: str) -> str:
    setup = client.post(f"/api/v1/auth/users/{user_id}/mfa/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirmed = client.post(
        f"/api/v1/auth/users/{user_id}/mfa/confirm",
        json={"code": mfa._totp(secret, int(time.time()) // 30)},
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


def test_admin_can_read_and_update_auth_policy(client: TestClient) -> None:
    initial = client.get("/api/v1/system/auth-policy")
    assert initial.status_code == 200
    assert initial.json()["login_failure_threshold"] == 5
    assert initial.json()["source"] == "环境变量"

    saved = client.put("/api/v1/system/auth-policy", json=policy_payload(
        login_failure_threshold=7,
        lock_duration_seconds=1200,
        session_ttl_seconds=1800,
    ))
    assert saved.status_code == 200, saved.text
    assert saved.json()["login_failure_threshold"] == 7
    assert saved.json()["lock_duration_seconds"] == 1200
    assert saved.json()["session_ttl_seconds"] == 1800
    assert saved.json()["source"] == "平台配置"
    assert client.get("/api/v1/system/auth-policy").json()["source"] == "平台配置"

    with client.app.state.session_factory() as session:
        audit = session.scalar(select(CmdbChangeAudit).where(
            CmdbChangeAudit.entity_type == "auth_policy",
        ))
        assert audit is not None
        assert audit.operator_username == "break-glass-admin"
        assert audit.after_snapshot["session_ttl_seconds"] == 1800


def test_invalid_policy_update_keeps_saved_values(client: TestClient) -> None:
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(login_failure_threshold=7),
    ).status_code == 200
    rejected = client.put(
        "/api/v1/system/auth-policy", json=policy_payload(login_failure_threshold=2),
    )
    assert rejected.status_code == 422
    assert client.get("/api/v1/system/auth-policy").json()["login_failure_threshold"] == 7


def test_login_reads_persisted_session_ttl_and_lock_threshold(client: TestClient) -> None:
    saved = client.put("/api/v1/system/auth-policy", json=policy_payload(
        login_failure_threshold=3,
        lock_duration_seconds=120,
        session_ttl_seconds=1800,
    ))
    assert saved.status_code == 200

    create_policy_user(client, "policy-login-user")
    login = client.post("/api/v1/auth/login", json={
        "username": "policy-login-user",
        "password": PASSWORD,
    })
    assert login.status_code == 200
    assert "Max-Age=1800" in login.headers["set-cookie"]
    client.post("/api/v1/auth/logout")

    for _ in range(3):
        failed = client.post("/api/v1/auth/login", json={
            "username": "policy-login-user",
            "password": "Wrong-Password-2026",
        })
        assert failed.status_code == 401
    locked = client.post("/api/v1/auth/login", json={
        "username": "policy-login-user",
        "password": PASSWORD,
    })
    assert locked.status_code == 401


def test_non_admin_cannot_read_or_update_auth_policy(client: TestClient) -> None:
    create_policy_user(client, "policy-operator")
    client.headers.pop("Authorization")
    logged_in = client.post("/api/v1/auth/login", json={
        "username": "policy-operator",
        "password": PASSWORD,
    })
    assert logged_in.status_code == 200
    assert client.get("/api/v1/system/auth-policy").status_code == 403
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(),
    ).status_code == 403


def test_corrupt_saved_policy_fails_login_closed_without_creating_session(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "corrupt-policy-user")
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(
            key="auth_policy", value='{"require_mfa":true,"broken":',
            updated_by="test-policy-corruption",
        ))
        session.commit()
    client.headers.pop("Authorization")
    response = client.post("/api/v1/auth/login", json={
        "username": "corrupt-policy-user", "password": PASSWORD,
    })
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert client.get("/api/v1/auth/me").status_code == 401
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id).where(AuthSession.user_id == user_id)) is None


def test_admin_can_repair_corrupt_saved_policy(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(
            key="auth_policy", value='{"require_mfa":true,"broken":',
            updated_by="test-policy-corruption",
        ))
        session.commit()

    repaired = client.put(
        "/api/v1/system/auth-policy",
        json=policy_payload(session_ttl_seconds=3600),
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["session_ttl_seconds"] == 3600
    with client.app.state.session_factory() as session:
        audit = session.scalar(select(CmdbChangeAudit).where(
            CmdbChangeAudit.entity_type == "auth_policy",
        ))
        assert audit is not None
        assert audit.before_snapshot == {"status": "unavailable"}


def test_environment_defaults_are_used_until_admin_saves_policy(client: TestClient) -> None:
    settings = client.app.state.runtime_settings
    settings.auth_lock_threshold = 8
    settings.auth_lock_seconds = 1500
    settings.auth_session_ttl_seconds = 7200
    initial = client.get("/api/v1/system/auth-policy")
    assert initial.status_code == 200
    assert initial.json() == {
        **policy_payload(
            login_failure_threshold=8,
            lock_duration_seconds=1500,
            session_ttl_seconds=7200,
        ),
        "source": "环境变量", "updated_by": "环境变量", "updated_at": None,
    }
    with client.app.state.session_factory() as session:
        assert session.get(PlatformSetting, "auth_policy") is None


def test_policy_ttl_change_only_affects_new_sessions(client: TestClient) -> None:
    user_id = create_policy_user(client, "session-ttl-user")
    credentials = {"username": "session-ttl-user", "password": PASSWORD}
    first_login = client.post("/api/v1/auth/login", json=credentials)
    assert first_login.status_code == 200
    old_cookie = client.cookies.get("sentinel_session")
    assert old_cookie
    with client.app.state.session_factory() as session:
        first = session.scalar(select(AuthSession).where(
            AuthSession.token_hash == hash_token(old_cookie),
        ))
        assert first is not None
        old_id, old_expiry = first.id, first.expires_at
    assert client.put("/api/v1/system/auth-policy", json=policy_payload(
        session_ttl_seconds=1800,
    )).status_code == 200
    second_login = client.post("/api/v1/auth/login", json=credentials)
    assert second_login.status_code == 200
    assert "Max-Age=1800" in second_login.headers["set-cookie"]
    with client.app.state.session_factory() as session:
        old_session = session.get(AuthSession, old_id)
        assert old_session is not None
        assert old_session.expires_at == old_expiry
        assert old_session.revoked_at is None
        latest = session.scalar(select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.id != old_id,
        ))
        assert latest is not None
        assert 1799 <= (latest.expires_at - latest.created_at).total_seconds() <= 1801
    client.headers.pop("Authorization")
    client.cookies.clear()
    client.cookies.set("sentinel_session", old_cookie)
    assert client.get("/api/v1/auth/me").status_code == 200
