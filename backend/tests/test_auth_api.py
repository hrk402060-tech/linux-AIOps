import base64
import time
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from src.config import Settings
from src.main import create_app
from src.models.auth import AuthSession, LoginAudit, User
from src.services import mfa
from src.models.ai_provider import AIProviderAudit
from src.models.asset_access import UserAccessAudit
from src.services.auth import hash_token


@pytest.fixture
def auth_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        credential_master_key=base64.b64encode(bytes(range(32))).decode("ascii"),
        credential_key_version="test-v1",
        api_admin_token="test-admin-token-with-at-least-32-characters",
        auto_create_schema=True,
        auth_bootstrap_username="security-admin",
        auth_bootstrap_password="Correct-Horse-Battery-2026",
        auth_cookie_secure=False,
        allowed_origins=(
            "http://localhost:8088,http://127.0.0.1:8088,"
            "http://localhost:5173,http://127.0.0.1:5173"
        ),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def login(client: TestClient, username: str = "security-admin",
          password: str = "Correct-Horse-Battery-2026",
          otp_code: str | None = None) -> dict[str, object]:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": password, "otp_code": otp_code,
    })
    assert response.status_code == 200
    return response.json()


def test_admin_can_enable_mfa_and_login_with_totp_or_recovery_code(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "mfa-user", "display_name": "MFA 用户",
        "password": "Mfa-Password-2026", "role": "operator",
    }).json()
    setup = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    code = mfa._totp(secret, int(time.time()) // 30)
    confirmed = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/mfa/confirm",
        json={"code": code},
    )
    assert confirmed.status_code == 200
    recovery_code = confirmed.json()["recovery_codes"][0]
    assert len(confirmed.json()["recovery_codes"]) == 8

    auth_client.post("/api/v1/auth/logout")
    required = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-user", "password": "Mfa-Password-2026",
    })
    assert required.status_code == 401
    assert required.json()["detail"] == "mfa_required"
    invalid = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-user", "password": "Mfa-Password-2026", "otp_code": "000000",
    })
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "mfa_invalid"
    logged_in = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-user", "password": "Mfa-Password-2026", "otp_code": recovery_code,
    })
    assert logged_in.status_code == 200
    assert logged_in.json()["mfa_enabled"] is True


def test_admin_can_reset_mfa_and_audit_it(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "mfa-reset-user", "password": "Mfa-Reset-2026!",
        "role": "operator",
    }).json()
    setup = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/setup")
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    assert auth_client.post(
        f"/api/v1/auth/users/{created['id']}/mfa/confirm", json={"code": code},
    ).status_code == 200
    reset = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/reset")
    assert reset.status_code == 200
    assert reset.json() == {"enabled": False}
    status = auth_client.get(f"/api/v1/auth/users/{created['id']}/mfa")
    assert status.json() == {"enabled": False}


def activate_admin(client: TestClient) -> dict[str, object]:
    login(client)
    response = client.post("/api/v1/auth/change-password", json={
        "current_password": "Correct-Horse-Battery-2026",
        "new_password": "Active-Admin-Password-2026",
    })
    assert response.status_code == 200
    return response.json()


def test_password_login_uses_http_only_server_session(auth_client: TestClient) -> None:
    user = login(auth_client)
    assert user["username"] == "security-admin"
    cookie = auth_client.cookies.get("sentinel_session")
    assert cookie
    response = auth_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "password_hash" not in response.text
    assert auth_client.get("/api/v1/assets").status_code == 403


def test_logout_revokes_session_and_clears_cookie(auth_client: TestClient) -> None:
    login(auth_client)
    response = auth_client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert auth_client.cookies.get("sentinel_session") is None
    assert auth_client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("origin", [
    "http://localhost:8088", "http://127.0.0.1:8088",
    "http://localhost:5173", "http://127.0.0.1:5173",
])
def test_allowed_local_origin_can_login(auth_client: TestClient, origin: str) -> None:
    response = auth_client.post("/api/v1/auth/login", headers={"Origin": origin}, json={
        "username": "security-admin", "password": "Correct-Horse-Battery-2026",
    })
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()


def test_auto_origin_allows_server_ip_without_fixed_allowlist() -> None:
    settings = Settings(
        database_url="sqlite://",
        credential_master_key=base64.b64encode(bytes(range(32))).decode("ascii"),
        api_admin_token="test-admin-token-with-at-least-32-characters",
        allowed_origins="auto",
    )
    assert settings.is_origin_allowed("http://192.168.110.215:8088")
    assert settings.is_origin_allowed("https://[2001:db8::10]:8443")
    assert not settings.is_origin_allowed("https://untrusted.example")


@pytest.mark.parametrize("headers", [
    {"Origin": "https://untrusted.example"},
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"},
])
def test_untrusted_login_origin_is_rejected(
    auth_client: TestClient, headers: dict[str, str],
) -> None:
    response = auth_client.post("/api/v1/auth/login", headers=headers, json={
        "username": "security-admin", "password": "Correct-Horse-Battery-2026",
    })
    assert response.status_code == 403
    assert auth_client.cookies.get("sentinel_session") is None


def test_invalid_password_is_audited_without_exposing_reason(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/login", json={
        "username": "security-admin", "password": "wrong-password-value",
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"
    with auth_client.app.state.session_factory() as session:
        audit = session.query(LoginAudit).one()
        assert audit.success is False
        assert audit.reason == "invalid_credentials"


def test_admin_can_unlock_a_user_after_login_lockout(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "locked-user",
        "password": "Locked-Password-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")

    for _ in range(5):
        failed = auth_client.post("/api/v1/auth/login", json={
            "username": "locked-user", "password": "Wrong-Password-2026",
        })
        assert failed.status_code == 401
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "locked-user", "password": "Locked-Password-2026",
    }).status_code == 401

    login(auth_client, "security-admin", "Active-Admin-Password-2026")
    locked = auth_client.get("/api/v1/auth/users").json()
    target = next(item for item in locked if item["id"] == created["id"])
    assert target["locked_until"] is not None
    assert target["failed_login_attempts"] == 0

    unlocked = auth_client.post(f"/api/v1/auth/users/{created['id']}/unlock")
    assert unlocked.status_code == 200
    assert unlocked.json()["locked_until"] is None
    assert unlocked.json()["failed_login_attempts"] == 0

    auth_client.post("/api/v1/auth/logout")
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "locked-user", "password": "Locked-Password-2026",
    }).status_code == 200


def test_admin_can_create_user_and_auditor_is_read_only(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "audit-user",
        "display_name": "审计用户",
        "password": "Audit-Only-Password-2026",
        "role": "auditor",
    })
    assert created.status_code == 201
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "audit-user", "Audit-Only-Password-2026")
    assert auth_client.get("/api/v1/assets").status_code == 200
    denied = auth_client.post("/api/v1/assets", json={})
    assert denied.status_code == 403


@pytest.mark.parametrize("password", [
    "Abcd1234",
    "1234567!",
    "Abcdefg!",
])
def test_new_password_requires_letters_numbers_and_special_character(
    auth_client: TestClient, password: str,
) -> None:
    activate_admin(auth_client)
    response = auth_client.post("/api/v1/auth/users", json={
        "username": "policy-user",
        "password": password,
        "role": "operator",
    })
    assert response.status_code == 422


def test_eight_character_password_can_login_and_change(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "short-policy-user",
        "password": "Abcd12!x",
        "role": "operator",
    })
    assert created.status_code == 201
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "short-policy-user", "Abcd12!x")
    changed = auth_client.post("/api/v1/auth/change-password", json={
        "current_password": "Abcd12!x",
        "new_password": "Zyxw98!q",
    })
    assert changed.status_code == 200


def test_admin_password_reset_only_forces_change_when_requested(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "reset-policy-user",
        "password": "Initial1!",
        "role": "operator",
    }).json()

    reset = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/reset-password",
        json={"password": "Reset123!"},
    )
    assert reset.status_code == 200
    assert reset.json()["must_change_password"] is False

    forced = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/reset-password",
        json={"password": "Forced123!", "must_change_password": True},
    )
    assert forced.status_code == 200
    assert forced.json()["must_change_password"] is True


def test_disabled_user_loses_existing_sessions(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "ops-user",
        "display_name": "运维用户",
        "password": "Operator-Password-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "ops-user", "Operator-Password-2026")
    operator_cookie = auth_client.cookies.get("sentinel_session")

    auth_client.cookies.clear()
    login(auth_client, "security-admin", "Active-Admin-Password-2026")
    disabled = auth_client.patch(f"/api/v1/auth/users/{created['id']}", json={
        "enabled": False,
    })
    assert disabled.status_code == 200

    auth_client.cookies.set("sentinel_session", operator_cookie)
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_business_audit_operator_comes_from_session(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    response = auth_client.post("/api/v1/agent/providers/config", json={
        "provider_id": "deepseek",
        "api_key": "sk-test-secret-value",
        "enabled": True,
        "operator": "forged-client-name",
    })
    assert response.status_code == 201
    with auth_client.app.state.session_factory() as session:
        audit = session.query(AIProviderAudit).one()
        assert audit.operator == "security-admin"


def test_break_glass_token_remains_available(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/api/v1/system/status",
        headers={"Authorization": "Bearer test-admin-token-with-at-least-32-characters"},
    )
    assert response.status_code == 200


def test_user_can_change_temporary_password(auth_client: TestClient) -> None:
    login(auth_client)
    changed = auth_client.post("/api/v1/auth/change-password", json={
        "current_password": "Correct-Horse-Battery-2026",
        "new_password": "New-Production-Password-2026",
    })
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    auth_client.post("/api/v1/auth/logout")
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "security-admin",
        "password": "Correct-Horse-Battery-2026",
    }).status_code == 401
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "security-admin",
        "password": "New-Production-Password-2026",
    }).status_code == 200


def test_login_audit_is_visible_and_last_admin_is_protected(
    auth_client: TestClient,
) -> None:
    user = activate_admin(auth_client)
    audit = auth_client.get("/api/v1/audit-log?source=authentication")
    assert audit.status_code == 200
    assert audit.json()["items"][0]["action"] == "login_succeeded"
    denied = auth_client.patch(f"/api/v1/auth/users/{user['id']}", json={
        "enabled": False,
    })
    assert denied.status_code == 409


def test_admin_can_list_active_sessions_without_token_material(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    response = auth_client.get("/api/v1/auth/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    item = sessions[0]
    assert item["username"] == "security-admin"
    assert item["status"] == "active"
    assert item["is_current"] is True
    assert "token_hash" not in item
    assert "cookie" not in str(item).lower()


def test_non_admin_cannot_list_or_revoke_sessions(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "session-operator",
        "password": "Operator-Password-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "session-operator", "Operator-Password-2026")
    denied = auth_client.get("/api/v1/auth/sessions")
    assert denied.status_code == 403
    assert auth_client.post(
        f"/api/v1/auth/users/{created['id']}/sessions/{uuid4()}/revoke"
    ).status_code == 403
    assert created["role"] == "operator"


def test_auditor_cannot_list_sessions(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    auth_client.post("/api/v1/auth/users", json={
        "username": "session-auditor",
        "password": "Auditor-Password-2026",
        "role": "auditor",
    })
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "session-auditor", "Auditor-Password-2026")
    assert auth_client.get("/api/v1/auth/sessions").status_code == 403


def test_admin_can_revoke_one_session_and_audit_it(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "session-target",
        "password": "Target-Password-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")
    login(auth_client, "session-target", "Target-Password-2026")
    target_cookie = auth_client.cookies.get("sentinel_session")
    auth_client.cookies.clear()
    login(auth_client, "security-admin", "Active-Admin-Password-2026")

    sessions = auth_client.get("/api/v1/auth/sessions").json()
    target = next(item for item in sessions if item["user_id"] == created["id"])
    assert auth_client.post(
        f"/api/v1/auth/users/{uuid4()}/sessions/{target['id']}/revoke"
    ).status_code == 404
    revoked = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/sessions/{target['id']}/revoke"
    )
    assert revoked.status_code == 204
    assert auth_client.post(
        f"/api/v1/auth/users/{created['id']}/sessions/{target['id']}/revoke"
    ).status_code == 404

    auth_client.cookies.set("sentinel_session", target_cookie)
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    auth_client.cookies.clear()
    login(auth_client, "security-admin", "Active-Admin-Password-2026")
    remaining = auth_client.get("/api/v1/auth/sessions").json()
    assert all(item["id"] != target["id"] for item in remaining)
    with auth_client.app.state.session_factory() as session:
        audit = session.query(UserAccessAudit).filter_by(
            user_id=created["id"], action="session_revoked",
        ).one()
        assert audit.operator_username == "security-admin"


def test_session_list_filters_expired_and_revoked_rows(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    now = datetime.now(timezone.utc)
    with auth_client.app.state.session_factory() as session:
        admin = session.query(User).filter_by(
            username="security-admin",
        ).one()
        session.add_all([
            AuthSession(
                id=str(uuid4()), token_hash=hash_token("expired-token"),
                user_id=admin.id, created_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1), last_used_at=now - timedelta(days=1),
            ),
            AuthSession(
                id=str(uuid4()), token_hash=hash_token("revoked-token"),
                user_id=admin.id, created_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1), last_used_at=now - timedelta(hours=1),
                revoked_at=now - timedelta(minutes=1),
            ),
        ])
        session.commit()
    response = auth_client.get("/api/v1/auth/sessions")
    assert response.status_code == 200
    assert all(item["status"] == "active" for item in response.json())
