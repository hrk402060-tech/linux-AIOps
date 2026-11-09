from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.models.asset import Asset
from src.models.credential import Credential
from sqlalchemy import func, select
from test_user_asset_access import ADMIN, assign, create_asset, create_user, sign_in


@pytest.mark.parametrize("action", ["update", "lifecycle"])
def test_asset_mutation_requires_exact_asset_grant(
    client: TestClient, action: str,
) -> None:
    user = create_user(client)
    allowed = create_asset(client, "allowed-write")
    hidden = create_asset(client, "hidden-write")
    assert assign(client, user, [{"asset_id": allowed}]).status_code == 200
    sign_in(client, user)
    path = f"/api/v1/assets/{hidden}"
    payload = {"name": "changed-without-access"}
    if action == "lifecycle":
        path += "/lifecycle"
        payload = {"status": "disabled", "reason": "maintenance request"}
    with patch(f"src.services.assets.{'update_asset' if action == 'update' else 'update_lifecycle'}") as write:
        assert client.patch(path, json=payload).status_code == 403
        write.assert_not_called()
    with client.app.state.session_factory() as session:
        asset = session.get(Asset, hidden)
        assert asset.name == "hidden-write"
        assert asset.lifecycle_status.value == "enabled"


def test_assigned_operator_cannot_create_or_bind_new_credentials(client: TestClient) -> None:
    user = create_user(client)
    sign_in(client, user)
    with client.app.state.session_factory() as session:
        before = session.scalar(select(func.count()).select_from(Credential))
    with patch("src.services.assets.create_asset") as create:
        assert client.post("/api/v1/assets", json={
            "name": "unapproved-host", "host": "10.0.0.99",
            "ssh_username": "ops", "ssh_secret": "test-only-inline-secret",
        }).status_code == 403
        create.assert_not_called()
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Credential)) == before


def test_all_asset_operator_can_create_and_assigned_operator_can_edit(client: TestClient) -> None:
    user = create_user(client)
    assert assign(client, user, [], asset_scope="all").status_code == 200
    sign_in(client, user)
    created = client.post("/api/v1/assets", json={"name": "operator-created", "host": "10.0.0.80"})
    assert created.status_code == 201
    asset_id = created.json()["id"]
    assert assign(client, user, [{"asset_id": asset_id}]).status_code == 200
    assert client.patch(f"/api/v1/assets/{asset_id}", json={
        "group_name": "authorized-group",
    }).status_code == 200
    assert client.patch(f"/api/v1/assets/{asset_id}/lifecycle", json={
        "status": "disabled", "reason": "planned maintenance",
    }).status_code == 200


def test_operator_cannot_mutate_or_archive_archived_asset(client: TestClient) -> None:
    user = create_user(client, username="archive-operator")
    asset_id = create_asset(client, "archive-write-boundary")
    assert assign(client, user, [], asset_scope="all").status_code == 200
    assert client.patch(f"/api/v1/assets/{asset_id}/lifecycle", headers=ADMIN, json={
        "status": "archived", "reason": "retired by administrator",
    }).status_code == 200

    sign_in(client, user)
    assert client.patch(
        f"/api/v1/assets/{asset_id}", json={"group_name": "must-stay-archived"}
    ).status_code == 403
    assert client.patch(
        f"/api/v1/assets/{asset_id}/lifecycle",
        json={"status": "enabled", "reason": "operator restore attempt"},
    ).status_code == 403
    assert client.patch(
        f"/api/v1/assets/{asset_id}/lifecycle",
        json={"status": "archived", "reason": "operator archive attempt"},
    ).status_code == 403

    assert client.patch(f"/api/v1/assets/{asset_id}/lifecycle", headers=ADMIN, json={
        "status": "enabled", "reason": "restored by administrator",
    }).status_code == 200


@pytest.mark.parametrize("change", [{"enabled": False}, {"role": "auditor"}])
def test_disabled_or_readonly_user_cannot_mutate_or_check(
    client: TestClient, change: dict[str, object],
) -> None:
    user = create_user(client)
    asset_id = create_asset(client, "account-state")
    assert assign(client, user, [{"asset_id": asset_id}]).status_code == 200
    sign_in(client, user)
    assert client.patch(f"/api/v1/auth/users/{user['id']}", headers=ADMIN, json=change).status_code == 200
    with patch("src.services.assets.check_tcp_connectivity") as network:
        assert client.patch(f"/api/v1/assets/{asset_id}", json={"name": "blocked"}).status_code in {401, 403}
        assert client.post(f"/api/v1/assets/{asset_id}/check").status_code in {401, 403}
        network.assert_not_called()
