from fastapi.testclient import TestClient
from httpx import Response
import pytest
from sqlalchemy import select

from src.models.auth import User
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services import access_policy, auth

PATH = "/api/v1/auth/access-policy"
PASSWORD = "access-policy-test!1"


def _account(client: TestClient, username: str, role: str = "operator") -> str:
    with client.app.state.session_factory() as session:
        user = User(username=username, password_hash=auth.password_hash.hash(PASSWORD), role=role)
        session.add(user)
        session.commit()
        return user.id


def _assignment(user_id: str, **values: list[str]) -> dict[str, object]:
    return {"user_id": user_id, "role_ids": [], "group_ids": [], "permissions": [], **values}


def _save(client: TestClient, current: dict, **values: list[dict]) -> Response:
    document = {key: current[key] for key in ("roles", "groups", "users")}
    return client.put(PATH, headers={"If-Match": current["revision"]}, json={**document, **values})


def test_role_group_and_direct_grants_are_unioned_and_audited(client: TestClient) -> None:
    user_id = _account(client, "feature-union")
    current = client.get(PATH).json()
    assert any(entry["page"] == "assets" for entry in current["catalog"])
    saved = _save(
        client, current,
        roles=[
            {"id": "role-assets", "name": "资产", "permissions": ["assets:read"]},
            {"id": "role-alerts", "name": "告警", "permissions": ["alerts:write"]},
        ],
        groups=[{"id": "group-ops", "name": "运维", "role_ids": ["role-alerts"]}],
        users=[_assignment(user_id, role_ids=["role-assets"], group_ids=["group-ops"], permissions=["cmdb:read"])],
    )
    assert saved.status_code == 200, saved.text
    assert client.get(PATH).json() == saved.json()
    with client.app.state.session_factory() as session:
        assert access_policy.effective_permissions(session, user_id, "operator") == {
            "alerts:read", "alerts:write", "assets:read", "cmdb:read",
        }
        assert access_policy.effective_permissions(session, user_id, "auditor") == {
            "alerts:read", "assets:read", "cmdb:read",
        }
        audit = session.scalar(select(CmdbChangeAudit).where(
            CmdbChangeAudit.entity_type == "platform_setting",
            CmdbChangeAudit.entity_id == access_policy.POLICY_KEY,
        ))
        assert audit is not None
        assert audit.operator_username == "break-glass-admin"
        assert audit.after_snapshot["users"][0]["user_id"] == user_id


def test_revision_required_and_lost_updates_do_not_change_policy(client: TestClient) -> None:
    user_id = _account(client, "feature-version")
    current = client.get(PATH).json()
    missing = client.put(PATH, json={"roles": [], "groups": [], "users": []})
    assert missing.status_code == 428
    saved = _save(client, current, users=[_assignment(user_id)])
    assert saved.status_code == 200, saved.text
    assert _save(client, current).status_code == 409
    assert client.get(PATH).json() == saved.json()


def test_managed_user_cannot_fall_back_to_legacy(client: TestClient) -> None:
    user_id = _account(client, "feature-monotonic")
    with client.app.state.session_factory() as session:
        assert access_policy.effective_permissions(session, user_id, "operator") is None
    saved = _save(client, client.get(PATH).json(), users=[_assignment(user_id)])
    assert saved.status_code == 200, saved.text
    assert _save(client, saved.json(), users=[]).status_code == 409
    with client.app.state.session_factory() as session:
        assert access_policy.effective_permissions(session, user_id, "operator") == set()


def test_new_account_initialization_is_transactional_and_idempotent(client: TestClient) -> None:
    user_id = _account(client, "feature-initialize")
    original = client.get(PATH).json()
    with client.app.state.session_factory() as session:
        access_policy.initialize_user(session, user_id)
        session.rollback()
        assert access_policy.effective_permissions(session, user_id, "operator") is None
        access_policy.initialize_user(session, user_id)
        session.commit()
        assert access_policy.effective_permissions(session, user_id, "operator") == set()
        access_policy.initialize_user(session, user_id)
        session.commit()
    assert client.get(PATH).json() == original


def test_api_created_non_admin_defaults_to_no_features(client: TestClient) -> None:
    created = client.post("/api/v1/auth/users", json={
        "username": "feature-new-account", "password": PASSWORD, "role": "operator",
    })
    assert created.status_code == 201, created.text
    with client.app.state.session_factory() as session:
        assert access_policy.effective_permissions(session, created.json()["id"], "operator") == set()


def test_invalid_links_and_reserved_permissions_are_rejected(client: TestClient) -> None:
    user_id = _account(client, "feature-link")
    admin_id = _account(client, "feature-admin", "admin")
    current = client.get(PATH).json()
    for bad_values in (
        {"users": [_assignment(admin_id, permissions=["assets:read"])]},
        {"users": [_assignment(user_id, permissions=["settings:write"])]},
        {"users": [_assignment(user_id, role_ids=["missing-role"])]},
        {"users": [_assignment(user_id, group_ids=["missing-group"])]},
        {"groups": [{"id": "group-ops", "name": "运维", "role_ids": ["missing-role"]}]},
    ):
        assert _save(client, current, **bad_values).status_code == 422
        assert client.get(PATH).json() == current


def test_access_policy_control_plane_is_not_delegable(client: TestClient) -> None:
    user_id = _account(client, "feature-viewer")
    saved = _save(client, client.get(PATH).json(), users=[_assignment(user_id, permissions=["assets:write"])])
    assert saved.status_code == 200, saved.text
    client.headers.pop("Authorization")
    assert client.get(PATH).status_code == 401
    login = client.post("/api/v1/auth/login", json={"username": "feature-viewer", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert client.get(PATH).status_code == 403
    assert client.put(PATH, headers={"If-Match": saved.json()["revision"]}, json={
        "roles": [], "groups": [], "users": [],
    }).status_code == 403


def test_corrupt_policy_fails_closed_without_overwrite(client: TestClient) -> None:
    user_id = _account(client, "feature-corrupt")
    with client.app.state.session_factory() as session:
        session.add(PlatformSetting(key=access_policy.POLICY_KEY, value='{"users": []}'))
        session.commit()
        with pytest.raises(access_policy.AccessPolicyUnavailableError):
            access_policy.effective_permissions(session, user_id, "operator")
    response = client.get(PATH)
    assert response.status_code == 503
    assert '"users"' not in response.text
