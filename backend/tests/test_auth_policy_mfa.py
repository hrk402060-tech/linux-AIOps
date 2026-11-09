from __future__ import annotations

import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.asset_access import UserAccessAudit
from src.models.auth import AuthSession, User
from src.models.platform_setting import PlatformSetting
from src.services import mfa
from test_auth_policy import PASSWORD, create_policy_user, enable_user_mfa, policy_payload


def test_force_mfa_is_rejected_until_enabled_users_are_bound(client: TestClient) -> None:
    create_policy_user(client, "unbound-mfa-user")
    response = client.put("/api/v1/system/auth-policy", json=policy_payload(require_mfa=True))
    assert response.status_code == 409
    assert "未绑定 MFA" in response.json()["detail"]


def test_force_mfa_blocks_new_unbound_user_with_specific_error(client: TestClient) -> None:
    saved = client.put("/api/v1/system/auth-policy", json=policy_payload(require_mfa=True))
    assert saved.status_code == 200
    create_policy_user(client, "new-unbound-user")
    client.headers.pop("Authorization")
    response = client.post("/api/v1/auth/login", json={
        "username": "new-unbound-user",
        "password": PASSWORD,
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "mfa_setup_required"
    assert "set-cookie" not in response.headers
    assert client.get("/api/v1/auth/me").status_code == 401


def test_missing_mfa_challenge_does_not_count_but_wrong_codes_lock_user(
    client: TestClient,
) -> None:
    assert client.put("/api/v1/system/auth-policy", json=policy_payload(
        login_failure_threshold=3, lock_duration_seconds=120,
    )).status_code == 200
    user_id = create_policy_user(client, "mfa-lock-user")
    secret = enable_user_mfa(client, user_id)
    client.headers.pop("Authorization")
    credentials = {"username": "mfa-lock-user", "password": PASSWORD}
    for _ in range(4):
        response = client.post("/api/v1/auth/login", json=credentials)
        assert response.status_code == 401
        assert response.json()["detail"] == "mfa_required"
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.failed_login_attempts == 0
        assert user.locked_until is None
    for _ in range(2):
        invalid = client.post(
            "/api/v1/auth/login", json={**credentials, "otp_code": "BADBAD"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "mfa_invalid"
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.failed_login_attempts == 2
        assert user.locked_until is None
    assert client.post(
        "/api/v1/auth/login", json={**credentials, "otp_code": "BADBAD"},
    ).status_code == 401
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.locked_until is not None
        assert session.scalar(select(AuthSession.id).where(AuthSession.user_id == user_id)) is None
    blocked = client.post("/api/v1/auth/login", json={
        **credentials, "otp_code": mfa._totp(secret, int(time.time()) // 30),
    })
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == "用户名或密码错误"


def test_forced_mfa_accepts_bound_user_and_rejects_later_unbound_user(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "bound-policy-user")
    secret = enable_user_mfa(client, user_id)
    enabled = client.put(
        "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
    )
    assert enabled.status_code == 200, enabled.text
    create_policy_user(client, "later-unbound-user")
    client.headers.pop("Authorization")
    challenge = client.post("/api/v1/auth/login", json={
        "username": "bound-policy-user", "password": PASSWORD,
    })
    assert challenge.status_code == 401
    assert challenge.json()["detail"] == "mfa_required"
    accepted = client.post("/api/v1/auth/login", json={
        "username": "bound-policy-user", "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    })
    assert accepted.status_code == 200, accepted.text
    assert client.post("/api/v1/auth/logout").status_code == 204
    rejected = client.post("/api/v1/auth/login", json={
        "username": "later-unbound-user", "password": PASSWORD,
    })
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "mfa_setup_required"
    assert "set-cookie" not in rejected.headers


def mfa_reset_state(client: TestClient, user_id: str) -> tuple[object, ...]:
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        return (
            (
                user.mfa_enabled, user.mfa_secret_encrypted, user.mfa_secret_nonce,
                user.mfa_key_version, user.mfa_recovery_code_hashes,
            ),
            list(session.execute(select(
                AuthSession.id, AuthSession.token_hash, AuthSession.expires_at,
                AuthSession.revoked_at,
            ).where(AuthSession.user_id == user_id).order_by(AuthSession.id))),
            list(session.scalars(select(UserAccessAudit.id).where(
                UserAccessAudit.user_id == user_id,
            ).order_by(UserAccessAudit.id))),
        )


def test_forced_mfa_preserves_last_bound_admin_and_existing_session(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "last-mfa-admin", role="admin")
    secret = enable_user_mfa(client, user_id)
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
    ).status_code == 200
    client.headers.pop("Authorization")
    assert client.post("/api/v1/auth/login", json={
        "username": "last-mfa-admin", "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    }).status_code == 200
    before = mfa_reset_state(client, user_id)
    rejected = client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    assert rejected.status_code == 409
    assert "先关闭强制 MFA" in rejected.json()["detail"]
    assert "另一名管理员绑定 MFA" in rejected.json()["detail"]
    assert mfa_reset_state(client, user_id) == before
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("keep_enforced", [True, False])
def test_admin_mfa_reset_allowed_with_bound_backup_or_disabled_policy(
    client: TestClient, keep_enforced: bool,
) -> None:
    user_id = create_policy_user(client, "reset-policy-admin", role="admin")
    secret = enable_user_mfa(client, user_id)
    if keep_enforced:
        backup_id = create_policy_user(client, "backup-mfa-admin", role="admin")
        enable_user_mfa(client, backup_id)
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
    ).status_code == 200
    assert client.post("/api/v1/auth/login", json={
        "username": "reset-policy-admin", "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    }).status_code == 200
    if not keep_enforced:
        assert client.put(
            "/api/v1/system/auth-policy", json=policy_payload(require_mfa=False),
        ).status_code == 200
    client.headers.pop("Authorization")
    reset = client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json()["enabled"] is False
    assert client.get("/api/v1/auth/me").status_code == 401
    with client.app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.mfa_secret_encrypted is None
        assert user.mfa_recovery_code_hashes is None
        assert session.scalar(select(AuthSession.id).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None),
        )) is None
        audit = session.scalar(select(UserAccessAudit).where(
            UserAccessAudit.user_id == user_id, UserAccessAudit.action == "mfa_reset",
        ))
        assert audit is not None
        assert audit.operator_username == "reset-policy-admin"


def test_corrupt_policy_rejects_mfa_reset_without_mutating_state(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "corrupt-reset-admin", role="admin")
    enable_user_mfa(client, user_id)
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(
            key="auth_policy", value='{"require_mfa":true,"broken":',
            updated_by="test-policy-corruption",
        ))
        session.commit()
    before = mfa_reset_state(client, user_id)
    rejected = client.post(f"/api/v1/auth/users/{user_id}/mfa/reset")
    assert rejected.status_code == 503
    assert mfa_reset_state(client, user_id) == before
