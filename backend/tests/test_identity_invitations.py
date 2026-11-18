from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from src.models.auth import AuthSession, User
from src.models.platform_setting import PlatformSetting
from src.services import identity_bindings, identity_store as store
from test_identity_login import LOGIN, prepare_directory
from test_identity_sources import BASE

SUBJECT = "invited-immutable-subject"
INVITATION = {
    "subject": SUBJECT, "username": "invited-auditor", "role": "auditor",
    "expires_hours": 1,
}


def prepare_invitation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_directory(client, monkeypatch)
    monkeypatch.setattr(
        "src.services.identity_ldap.authenticate",
        lambda *_: {"subject": SUBJECT, "display_name": "Invited User"},
    )
    response = client.post(f"{BASE}/ldap/invitations", json=INVITATION)
    assert response.status_code == 201, response.text


def test_invited_user_gets_only_assigned_asset_permissions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_invitation(client, monkeypatch)
    settings = client.get("/api/v1/system/settings").json()["settings"]["authentication"]
    saved = client.put("/api/v1/system/settings/authentication", json={
        **settings, "new_account_policy": "invite_only",
    })
    assert saved.status_code == 200, saved.text
    result = client.post(LOGIN, json={"username": "remote", "password": "Directory-Pass"})
    assert result.status_code == 200, result.text
    assert result.json()["username"] == INVITATION["username"]
    assert result.json()["role"] == "auditor"
    assert result.json()["asset_scope"] == "assigned"
    repeated = client.post(LOGIN, json={"username": "remote", "password": "Directory-Pass"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == result.json()["id"]


@pytest.mark.parametrize("restricted_by", ["policy", "expiration"])
def test_invitation_restrictions_prevent_account_creation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, restricted_by: str,
) -> None:
    prepare_invitation(client, monkeypatch)
    if restricted_by == "expiration":
        with client.app.state.session_factory() as session:
            policy = store.authentication_settings(session)
            store.put(session, "settings.authentication", {
                **policy, "new_account_policy": "invite_only",
            })
            binding_key = identity_bindings.key("ldap", SUBJECT)
            binding = store.load(session, binding_key)
            assert binding is not None
            binding["expires_at"] = (store.utcnow() - timedelta(minutes=1)).isoformat()
            store.put(session, binding_key, binding)
            store.commit(session)
    result = client.post(LOGIN, json={"username": "remote", "password": "Directory-Pass"})
    assert result.status_code == 403, result.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(User.id).where(
            User.username == INVITATION["username"],
        )) is None


def test_invitation_cannot_grant_administrator_role(client: TestClient) -> None:
    result = client.post(f"{BASE}/ldap/invitations", json={**INVITATION, "role": "admin"})
    assert result.status_code == 422, result.text


def test_unlinking_external_identity_revokes_its_platform_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = prepare_directory(client, monkeypatch)
    result = client.post(LOGIN, json={"username": "remote", "password": "Directory-Pass"})
    assert result.status_code == 200, result.text
    bindings = client.get(f"{BASE}/ldap/bindings").json()
    response = client.delete(
        f"{BASE}/ldap/bindings", params={"subject": bindings[0]["subject"]},
    )
    assert response.status_code == 204, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(AuthSession.id).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None),
        )) is None
        assert session.get(PlatformSetting, identity_bindings.key(
            "ldap", bindings[0]["subject"],
        )) is None
    client.headers.pop("Authorization")
    assert client.get("/api/v1/auth/me").status_code == 401
