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
from test_auth_policy_mfa import mfa_reset_state


def login_bound_admin(client: TestClient, username: str, secret: str) -> None:
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": PASSWORD,
        "otp_code": mfa._totp(secret, int(time.time()) // 30),
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("changes", [
    {"enabled": False}, {"role": "operator"}, {"role": "auditor"},
])
def test_cannot_disable_or_demote_last_bound_admin_after_another_admin_reset(
    client: TestClient, changes: dict[str, object],
) -> None:
    target_id = create_policy_user(client, "last-bound-target", role="admin")
    target_secret = enable_user_mfa(client, target_id)
    other_id = create_policy_user(client, "reset-other-admin", role="admin")
    enable_user_mfa(client, other_id)
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(require_mfa=True),
    ).status_code == 200
    login_bound_admin(client, "last-bound-target", target_secret)
    assert client.post(f"/api/v1/auth/users/{other_id}/mfa/reset").status_code == 200
    before = mfa_reset_state(client, target_id)
    response = client.patch(f"/api/v1/auth/users/{target_id}", json=changes)
    assert response.status_code == 409, response.text
    assert "不能停用或降级最后一名" in response.json()["detail"]
    assert mfa_reset_state(client, target_id) == before
    with client.app.state.session_factory() as session:
        target = session.get(User, target_id)
        assert target is not None
        assert target.enabled is True
        assert target.role == "admin"
    client.headers.pop("Authorization")
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("changes", [{"enabled": False}, {"role": "operator"}])
@pytest.mark.parametrize("require_mfa", [True, False])
def test_admin_changes_allowed_with_bound_survivor_or_without_enforcement(
    client: TestClient, changes: dict[str, object], require_mfa: bool,
) -> None:
    target_id = create_policy_user(client, "allowed-admin-target", role="admin")
    secret = enable_user_mfa(client, target_id)
    survivor_id = create_policy_user(client, "surviving-admin", role="admin")
    if require_mfa:
        enable_user_mfa(client, survivor_id)
    assert client.put(
        "/api/v1/system/auth-policy", json=policy_payload(require_mfa=require_mfa),
    ).status_code == 200
    login_bound_admin(client, "allowed-admin-target", secret)
    response = client.patch(f"/api/v1/auth/users/{target_id}", json=changes)
    assert response.status_code == 200, response.text
    assert response.json()["role"] == changes.get("role", "admin")
    assert response.json()["enabled"] == changes.get("enabled", True)
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id).where(
            AuthSession.user_id == target_id, AuthSession.revoked_at.is_(None),
        )) is None
        survivor = session.get(User, survivor_id)
        assert survivor is not None
        assert survivor.role == "admin"
        assert survivor.enabled is True
        assert survivor.mfa_enabled is require_mfa
        audit = session.scalar(select(UserAccessAudit).where(
            UserAccessAudit.user_id == target_id, UserAccessAudit.action == "user_updated",
        ))
        assert audit is not None
        assert audit.before_snapshot["role"] == "admin"
        assert audit.after_snapshot["role"] == changes.get("role", "admin")


def test_corrupt_policy_blocks_admin_removal_without_mutating_user_or_audit(
    client: TestClient,
) -> None:
    user_id = create_policy_user(client, "corrupt-removal-admin", role="admin")
    enable_user_mfa(client, user_id)
    create_policy_user(client, "other-removal-admin", role="admin")
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(
            key="auth_policy", value='{"require_mfa":true,"broken":',
            updated_by="test-corruption",
        ))
        session.commit()
    before = mfa_reset_state(client, user_id)
    response = client.patch(f"/api/v1/auth/users/{user_id}", json={"enabled": False})
    assert response.status_code == 503, response.text
    assert mfa_reset_state(client, user_id) == before
    with client.app.state.session_factory() as session:
        assert session.get(User, user_id).enabled is True
