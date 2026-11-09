import base64
import time
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapper
from sqlalchemy.engine import Connection

from src.config import Settings
from src.main import create_app
from src.models.asset_access import UserAccessAudit
from src.models.auth import AuthSession, LoginAudit, User
from src.models.terminal import TerminalSession
from src.services import mfa


@pytest.fixture
def auth_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'auth-governance.db'}",
        credential_master_key=base64.b64encode(bytes(range(32))).decode("ascii"),
        credential_key_version="test-v1",
        api_admin_token="test-admin-token-with-at-least-32-characters",
        auto_create_schema=True,
        auth_bootstrap_username="security-admin",
        auth_bootstrap_password="Correct-Horse-Battery-2026",
        auth_cookie_secure=False,
        allowed_origins="http://testserver",
        diagnostic_workers_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def activate_admin(client: TestClient) -> None:
    logged_in = client.post("/api/v1/auth/login", json={
        "username": "security-admin",
        "password": "Correct-Horse-Battery-2026",
    })
    assert logged_in.status_code == 200
    changed = client.post("/api/v1/auth/change-password", json={
        "current_password": "Correct-Horse-Battery-2026",
        "new_password": "Active-Admin-Password-2026",
    })
    assert changed.status_code == 200


def create_mfa_user(client: TestClient) -> tuple[str, str]:
    created = client.post("/api/v1/auth/users", json={
        "username": "mfa-session-user",
        "password": "Mfa-Session-Password-2026",
        "role": "operator",
    })
    assert created.status_code == 201
    user_id = created.json()["id"]
    setup = client.post(f"/api/v1/auth/users/{user_id}/mfa/setup")
    assert setup.status_code == 200
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    confirmed = client.post(
        f"/api/v1/auth/users/{user_id}/mfa/confirm", json={"code": code},
    )
    assert confirmed.status_code == 200
    return user_id, confirmed.json()["recovery_codes"][0]


def test_resetting_mfa_revokes_existing_user_sessions(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "mfa-session-user",
        "password": "Mfa-Session-Password-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")
    logged_in = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-session-user",
        "password": "Mfa-Session-Password-2026",
    })
    assert logged_in.status_code == 200
    user_cookie = auth_client.cookies.get("sentinel_session")
    assert user_cookie

    auth_client.cookies.clear()
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "security-admin",
        "password": "Active-Admin-Password-2026",
    }).status_code == 200
    setup = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/setup")
    assert setup.status_code == 200
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    assert auth_client.post(
        f"/api/v1/auth/users/{created['id']}/mfa/confirm", json={"code": code},
    ).status_code == 200
    reset = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/reset")
    assert reset.status_code == 200

    auth_client.cookies.set("sentinel_session", user_cookie)
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_enabling_mfa_revokes_existing_user_sessions(auth_client: TestClient) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "mfa-enable-session-user",
        "password": "Mfa-Enable-Session-2026",
        "role": "operator",
    }).json()
    auth_client.post("/api/v1/auth/logout")
    logged_in = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-enable-session-user",
        "password": "Mfa-Enable-Session-2026",
    })
    assert logged_in.status_code == 200
    user_cookie = auth_client.cookies.get("sentinel_session")
    assert user_cookie

    auth_client.cookies.clear()
    assert auth_client.post("/api/v1/auth/login", json={
        "username": "security-admin",
        "password": "Active-Admin-Password-2026",
    }).status_code == 200
    setup = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/setup")
    assert setup.status_code == 200
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    confirmed = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/mfa/confirm", json={"code": code},
    )
    assert confirmed.status_code == 200

    auth_client.cookies.set("sentinel_session", user_cookie)
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    with auth_client.app.state.session_factory() as session:
        assert session.query(AuthSession).filter_by(
            user_id=created["id"], revoked_at=None,
        ).count() == 0


def test_authentication_audit_supports_ip_reason_and_status_filters(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    failed = auth_client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "192.0.2.44"},
        json={"username": "security-admin", "password": "Wrong-Password-2026"},
    )
    assert failed.status_code == 401
    response = auth_client.get(
        "/api/v1/audit-log",
        params={
            "source": "authentication",
            "client_ip": "192.0.2.44",
            "failure_reason": "invalid_credentials",
            "login_success": "false",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "login_failed"
    assert body["summary"] == {
        "authentication_total": 1,
        "login_succeeded": 0,
        "login_failed": 1,
        "locked": 0,
        "mfa_failed": 0,
    }


def test_authentication_summary_counts_mfa_setup_required(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    with auth_client.app.state.session_factory() as session:
        session.add(LoginAudit(
            username="unbound-user", success=False,
            reason="mfa_setup_required", client_ip="192.0.2.50",
        ))
        session.commit()
    response = auth_client.get("/api/v1/audit-log")
    assert response.status_code == 200
    assert response.json()["summary"]["mfa_failed"] == 1


def test_incorrect_mfa_confirmation_returns_400_without_enabling_mfa(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    created = auth_client.post("/api/v1/auth/users", json={
        "username": "invalid-confirmation", "password": "Invalid-Mfa-2026!",
        "role": "operator",
    }).json()
    setup = auth_client.post(f"/api/v1/auth/users/{created['id']}/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    invalid_code = next(
        f"{value:06d}" for value in range(100)
        if not mfa.verify_totp(secret, f"{value:06d}")
    )
    response = auth_client.post(
        f"/api/v1/auth/users/{created['id']}/mfa/confirm",
        json={"code": invalid_code},
    )
    assert response.status_code == 400
    with auth_client.app.state.session_factory() as session:
        assert session.get(User, created["id"]).mfa_enabled is False
        assert session.query(UserAccessAudit).filter_by(
            user_id=created["id"], action="mfa_enabled",
        ).count() == 0


def test_mfa_reset_rolls_back_with_its_audit_and_session_revocations(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    user_id, recovery_code = create_mfa_user(auth_client)
    admin_cookie = auth_client.cookies.get("sentinel_session")
    assert admin_cookie
    auth_client.cookies.clear()
    logged_in = auth_client.post("/api/v1/auth/login", json={
        "username": "mfa-session-user", "password": "Mfa-Session-Password-2026",
        "otp_code": recovery_code,
    })
    assert logged_in.status_code == 200
    user_cookie = auth_client.cookies.get("sentinel_session")
    assert user_cookie
    auth_client.cookies.clear()
    auth_client.cookies.set("sentinel_session", admin_cookie)

    def fail_audit(
        _mapper: Mapper[UserAccessAudit], _connection: Connection,
        target: UserAccessAudit,
    ) -> None:
        if target.action == "mfa_reset":
            raise SQLAlchemyError("simulated audit write failure")

    event.listen(UserAccessAudit, "before_insert", fail_audit)
    try:
        response = auth_client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    finally:
        event.remove(UserAccessAudit, "before_insert", fail_audit)
    assert response.status_code == 503
    with auth_client.app.state.session_factory() as session:
        assert session.get(User, user_id).mfa_enabled is True
        assert session.query(AuthSession).filter_by(
            user_id=user_id, revoked_at=None,
        ).count() == 1
        assert session.query(UserAccessAudit).filter_by(
            user_id=user_id, action="mfa_reset",
        ).count() == 0
    auth_client.cookies.set("sentinel_session", user_cookie)
    assert auth_client.get("/api/v1/auth/me").status_code == 200


def test_mfa_reset_revokes_waiting_tickets_and_signals_connected_terminals(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    user_id, _ = create_mfa_user(auth_client)
    asset = auth_client.post("/api/v1/assets", json={
        "name": "mfa-terminal-node", "host": "192.0.2.90",
        "os_family": "openeuler", "environment": "test",
    })
    assert asset.status_code == 201
    with auth_client.app.state.session_factory() as session:
        for status in ("waiting", "connected"):
            session.add(TerminalSession(
                id=f"mfa-{status}", asset_id=asset.json()["id"],
                asset_name="mfa-terminal-node", host="192.0.2.90",
                operator="mfa-session-user", client_ip="192.0.2.91",
                status=status, ticket_expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=1),
            ))
        session.commit()
    registry = auth_client.app.state.terminal_connections
    stop = registry.acquire("mfa-connected")
    assert stop is not None
    response = auth_client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    assert response.status_code == 200
    assert stop.is_set()
    with auth_client.app.state.session_factory() as session:
        assert session.get(TerminalSession, "mfa-waiting").status == "revoked"


@pytest.mark.parametrize("filters,expected_reasons", [
    ({"client_ip": " 192.0.2.4 "}, {"locked"}),
    ({"failure_reason": " locked "}, {"locked"}),
    ({"login_success": "false"}, {"locked", "unlocked"}),
])
def test_login_filters_exclude_unrelated_audits_and_partial_matches(
    auth_client: TestClient, filters: dict[str, str], expected_reasons: set[str],
) -> None:
    activate_admin(auth_client)
    with auth_client.app.state.session_factory() as session:
        session.add_all([
            LoginAudit(
                username="locked-user", success=False, reason="locked",
                client_ip="192.0.2.4",
            ),
            LoginAudit(
                username="other-user", success=False, reason="unlocked",
                client_ip="192.0.2.44",
            ),
            UserAccessAudit(
                user_id="unrelated-user", username="unrelated-user",
                action="user_updated", operator_username="security-admin",
                reason="unrelated business audit",
            ),
        ])
        session.commit()
    response = auth_client.get("/api/v1/audit-log", params=filters)
    assert response.status_code == 200
    assert response.json()["total"] == len(expected_reasons)
    assert all(item["source"] == "authentication" for item in response.json()["items"])
    assert {item["detail"] for item in response.json()["items"]} == expected_reasons


def test_failure_reason_excludes_success_events_and_composes_with_source(
    auth_client: TestClient,
) -> None:
    activate_admin(auth_client)
    with auth_client.app.state.session_factory() as session:
        session.add(LoginAudit(
            username="inconsistent-success", success=True,
            reason="locked", client_ip="192.0.2.4",
        ))
        session.commit()
    response = auth_client.get("/api/v1/audit-log", params={"failure_reason": "locked"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    response = auth_client.get("/api/v1/audit-log", params={
        "source": "user_management", "login_success": "true",
    })
    assert response.status_code == 200
    assert response.json()["total"] == 0
