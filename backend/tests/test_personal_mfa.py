from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.auth import AuthSession, User

from src.services import mfa
from test_auth_policy import PASSWORD, create_policy_user, enable_user_mfa, policy_payload


def test_user_can_bind_mfa_from_personal_endpoint_with_qr_payload(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "personal-mfa-user")
    client.headers.pop("Authorization")
    logged_in = client.post("/api/v1/auth/login", json={
        "username": "personal-mfa-user", "password": PASSWORD,
    })
    assert logged_in.status_code == 200

    status = client.get("/api/v1/auth/me/mfa")
    assert status.status_code == 200
    assert status.json() == {
        "enabled": False, "enforcement": "off",
        "required": False, "can_disable": True,
    }
    setup = client.post("/api/v1/auth/me/mfa/setup")
    assert setup.status_code == 200
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    code = mfa._totp(setup.json()["secret"], int(time.time()) // 30)
    confirmed = client.post("/api/v1/auth/me/mfa/confirm", json={"code": code})
    assert confirmed.status_code == 200
    assert len(confirmed.json()["recovery_codes"]) == 8
    assert client.get("/api/v1/auth/me").status_code == 401

    relogin = client.post("/api/v1/auth/login", json={
        "username": "personal-mfa-user", "password": PASSWORD, "otp_code": code,
    })
    assert relogin.status_code == 200
    assert client.get("/api/v1/auth/me/mfa").json()["enabled"] is True
    assert user_id


def test_admin_only_enforcement_keeps_operator_login_optional(
    client: TestClient,
) -> None:
    admin_id = create_policy_user(client, "mfa-policy-admin", role="admin")
    create_policy_user(client, "mfa-policy-operator")
    enable_user_mfa(client, admin_id)
    saved = client.put("/api/v1/system/auth-policy", json=policy_payload(
        mfa_enforcement="admins", require_mfa=False,
    ))
    assert saved.status_code == 200, saved.text
    assert saved.json()["mfa_enforcement"] == "admins"
    assert saved.json()["require_mfa"] is True

    client.headers.pop("Authorization")
    operator_login = client.post("/api/v1/auth/login", json={
        "username": "mfa-policy-operator", "password": PASSWORD,
    })
    assert operator_login.status_code == 200
    client.post("/api/v1/auth/logout")
    admin_setup_required = client.post("/api/v1/auth/login", json={
        "username": "mfa-policy-admin", "password": PASSWORD,
    })
    assert admin_setup_required.status_code == 401
    assert admin_setup_required.json()["detail"] == "mfa_required"


def test_forced_mfa_login_can_start_and_confirm_qr_binding(
    client: TestClient,
) -> None:
    admin_id = create_policy_user(client, "forced-enroll-admin", role="admin")
    enable_user_mfa(client, admin_id)
    create_policy_user(client, "forced-enroll-user")
    assert client.put("/api/v1/system/auth-policy", json=policy_payload(
        mfa_enforcement="all", require_mfa=False,
    )).status_code == 200
    client.headers.pop("Authorization")
    blocked = client.post("/api/v1/auth/login", json={
        "username": "forced-enroll-user", "password": PASSWORD,
    })
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == "mfa_setup_required"
    enrollment = client.post("/api/v1/auth/mfa/enroll/start", json={
        "username": "forced-enroll-user", "password": PASSWORD,
    })
    assert enrollment.status_code == 200, enrollment.text
    body = enrollment.json()
    assert body["enrollment_token"]
    code = mfa._totp(body["secret"], int(time.time()) // 30)
    confirmed = client.post("/api/v1/auth/mfa/enroll/confirm", json={
        "enrollment_token": body["enrollment_token"], "code": code,
    })
    assert confirmed.status_code == 200, confirmed.text
    login = client.post("/api/v1/auth/login", json={
        "username": "forced-enroll-user", "password": PASSWORD, "otp_code": code,
    })
    assert login.status_code == 200
    assert login.json()["mfa_enabled"] is True


def start_enrollment(client: TestClient) -> tuple[str, dict[str, str]]:
    user_id = create_policy_user(client, "enrollment-security-user")
    assert client.put("/api/v1/system/auth-policy", json=policy_payload(
        mfa_enforcement="all", login_failure_threshold=3,
    )).status_code == 200
    client.headers.pop("Authorization")
    started = client.post("/api/v1/auth/mfa/enroll/start", json={
        "username": "enrollment-security-user", "password": PASSWORD,
    })
    assert started.status_code == 200, started.text
    assert "set-cookie" not in started.headers
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id).where(
            AuthSession.user_id == user_id,
        )) is None
    return user_id, started.json()


def test_enrollment_token_is_single_use(client: TestClient) -> None:
    user_id, body = start_enrollment(client)
    payload = {
        "enrollment_token": body["enrollment_token"],
        "code": mfa._totp(body["secret"], int(time.time()) // 30),
    }
    first = client.post("/api/v1/auth/mfa/enroll/confirm", json=payload)
    assert first.status_code == 200
    with client.app.state.session_factory() as session:
        hashes = session.get(User, user_id).mfa_recovery_code_hashes
    assert client.post("/api/v1/auth/mfa/enroll/confirm", json=payload).status_code == 401
    with client.app.state.session_factory() as session:
        assert session.get(User, user_id).mfa_recovery_code_hashes == hashes


@pytest.mark.parametrize("change", ["password", "disabled", "revision", "new_setup"])
def test_enrollment_invalidates_on_account_or_setup_change(
    client: TestClient, change: str,
) -> None:
    user_id, body = start_enrollment(client)
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        if change == "password":
            user.password_hash = "new-password-hash"
        elif change == "disabled":
            user.enabled = False
        elif change == "revision":
            user.access_revision += 1
        else:
            mfa.setup(session, user, client.app.state.credential_vault)
        session.commit()
    response = client.post("/api/v1/auth/mfa/enroll/confirm", json={
        "enrollment_token": body["enrollment_token"],
        "code": mfa._totp(body["secret"], int(time.time()) // 30),
    })
    assert response.status_code == 401
    with client.app.state.session_factory() as session:
        assert not session.get(User, user_id).mfa_enabled


def test_enrollment_wrong_codes_lock_account_without_enabling_mfa(client: TestClient) -> None:
    user_id, body = start_enrollment(client)
    for _ in range(3):
        response = client.post("/api/v1/auth/mfa/enroll/confirm", json={
            "enrollment_token": body["enrollment_token"], "code": "WRONG!",
        })
        assert response.status_code == 400
    blocked = client.post("/api/v1/auth/mfa/enroll/confirm", json={
        "enrollment_token": body["enrollment_token"],
        "code": mfa._totp(body["secret"], int(time.time()) // 30),
    })
    assert blocked.status_code == 401
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user.locked_until is not None
        assert user.mfa_enabled is False
        assert user.mfa_recovery_code_hashes is None


def test_enrollment_rejects_cross_origin_and_tampered_tokens(client: TestClient) -> None:
    _, body = start_enrollment(client)
    payload = {
        "enrollment_token": body["enrollment_token"],
        "code": mfa._totp(body["secret"], int(time.time()) // 30),
    }
    assert client.post("/api/v1/auth/mfa/enroll/confirm", json=payload,
                       headers={"Origin": "https://outside.invalid"}).status_code == 403
    assert client.post("/api/v1/auth/mfa/enroll/confirm", json={
        **payload, "enrollment_token": "malformed!" * 3,
    }).status_code == 401


def test_user_cannot_disable_mfa_when_admin_forces_it(client: TestClient) -> None:
    user_id = create_policy_user(client, "cannot-reset-mfa")
    secret = enable_user_mfa(client, user_id)
    assert client.put("/api/v1/system/auth-policy", json=policy_payload(
        mfa_enforcement="all",
    )).status_code == 200
    client.headers.pop("Authorization")
    assert client.post("/api/v1/auth/login", json={
        "username": "cannot-reset-mfa", "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    }).status_code == 200
    assert client.post("/api/v1/auth/me/mfa/reset").status_code == 409
    assert client.get("/api/v1/auth/me/mfa").json()["enabled"] is True
