from collections import deque
import base64
import hashlib
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import paramiko
import pytest
from sqlalchemy import select

from src.models.asset_access import UserAccessAudit
from src.models.ssh_host_key import SshHostKey
from src.services.ssh_host_keys import HostKeyCandidate

ADMIN = {"Authorization": "Bearer test-admin-token-with-at-least-32-characters"}


class FakeShell:
    def __init__(self) -> None:
        self.output: deque[bytes] = deque([b"ready\r\n"])
        self.sent: list[str] = []
        self.active = True

    def receive(self) -> bytes:
        return self.output.popleft() if self.output else b""

    def has_output(self) -> bool:
        return bool(self.output)

    def is_active(self) -> bool:
        return self.active

    def send(self, value: str) -> None:
        self.sent.append(value)
        self.active = False

    def resize(self, columns: int, rows: int) -> None:
        del columns, rows

    def close(self) -> None:
        self.active = False


def _register_host_key(client: TestClient, asset_id: str) -> HostKeyCandidate:
    key = paramiko.RSAKey.generate(1024)
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
    candidate = HostKeyCandidate(
        key.get_name(), f"SHA256:{digest}", f"{key.get_name()} {key.get_base64()}"
    )
    with client.app.state.session_factory() as session:
        session.add(SshHostKey(
            id=str(uuid4()), asset_id=asset_id, host="10.0.0.31", port=22,
            key_type=candidate.key_type, fingerprint=candidate.fingerprint,
            public_key=candidate.public_key, confirmed_by="test-admin",
        ))
        session.commit()
    return candidate


def create_user(client: TestClient, username: str = "scoped-user", role: str = "operator") -> dict:
    response = client.post("/api/v1/auth/users", headers=ADMIN, json={
        "username": username, "password": "Scope-Test-2026!", "role": role,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_asset(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/assets", headers=ADMIN, json={
        "name": name, "host": "10.0.0.31", "ssh_username": "ops",
        "ssh_secret": "test-ssh-secret",
    })
    assert response.status_code == 201, response.text
    asset_id = response.json()["id"]
    _register_host_key(client, asset_id)
    return asset_id


def assign(
    client: TestClient, user: dict, entries: list[dict], **overrides: object
):
    current = client.get(f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN)
    assert current.status_code == 200
    return client.put(f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN, json={
        "asset_scope": "assigned", "expected_revision": current.json()["revision"],
        "entries": entries, "confirmation": user["username"],
        "reason": "restrict to assigned production assets", **overrides,
    })


def sign_in(client: TestClient, user: dict) -> None:
    client.headers.pop("authorization", None)
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={
        "username": user["username"], "password": "Scope-Test-2026!",
    })
    assert response.status_code == 200


def test_new_user_denied_by_default_and_grants_do_not_leak_assets(client: TestClient) -> None:
    user = create_user(client)
    granted = create_asset(client, "allowed-node")
    hidden = create_asset(client, "hidden-node")
    sign_in(client, user)
    assert client.get("/api/v1/assets").json() == []
    response = assign(client, user, [{"asset_id": granted, "can_ssh": False}])
    assert response.status_code == 200
    assert [item["id"] for item in client.get("/api/v1/assets?lifecycle=all").json()] == [granted]
    assert hidden not in client.get("/api/v1/assets").text
    with patch("src.services.assets.check_tcp_connectivity", return_value=(True, 1, "ok")) as network:
        assert client.post(f"/api/v1/assets/{hidden}/check").status_code == 403
        assert client.post(f"/api/v1/assets/{granted}/check").status_code == 200
        assert client.post("/api/v1/terminal/tickets", json={"asset_id": granted}).status_code == 403
        network.assert_called_once()
    assert client.get(f"/api/v1/assets/{hidden}/cleanup-preview").status_code == 403


@pytest.mark.parametrize("confirmation, expected", [("wrong", 409), ("  scoped-user  ", 200)])
def test_exact_confirmation_is_normalized(
    client: TestClient, confirmation: str, expected: int
) -> None:
    user = create_user(client)
    asset = create_asset(client, "confirmation-node")
    response = assign(client, user, [{"asset_id": asset}], confirmation=confirmation)
    assert response.status_code == expected
    with client.app.state.session_factory() as session:
        audits = session.scalars(select(UserAccessAudit).where(
            UserAccessAudit.action == "asset_access_updated"
        )).all()
        assert len(audits) == (1 if expected == 200 else 0)


def test_revision_duplicate_and_missing_asset_reject_without_partial_grant(client: TestClient) -> None:
    user = create_user(client)
    asset = create_asset(client, "revision-node")
    assert assign(client, user, [{"asset_id": asset, "can_ssh": True}]).status_code == 200
    assert assign(client, user, [], expected_revision=0).status_code == 409
    assert assign(client, user, [{"asset_id": asset}, {"asset_id": asset}]).status_code == 422
    assert assign(client, user, [{"asset_id": "nonexistent"}]).status_code == 404
    current = client.get(f"/api/v1/auth/users/{user['id']}/asset-access", headers=ADMIN).json()
    assert current["entries"] == [{"asset_id": asset, "can_ssh": True}]
    assert current["revision"] == 1


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_non_admin_cannot_edit_permissions_or_accounts(client: TestClient, role: str) -> None:
    user = create_user(client, role=role)
    sign_in(client, user)
    assert client.get("/api/v1/auth/users").status_code == 403
    assert client.patch(f"/api/v1/auth/users/{user['id']}", json={"role": "admin"}).status_code == 403
    assert client.put(f"/api/v1/auth/users/{user['id']}/asset-access", json={
        "asset_scope": "all", "expected_revision": 0,
        "confirmation": user["username"], "reason": "attempt privilege escalation",
    }).status_code == 403


def test_auditor_cannot_receive_ssh_grant_or_create_ticket(client: TestClient) -> None:
    user = create_user(client, role="auditor")
    asset = create_asset(client, "audit-node")
    assert assign(client, user, [{"asset_id": asset, "can_ssh": True}]).status_code == 409
    assert assign(client, user, [{"asset_id": asset, "can_ssh": False}]).status_code == 200
    sign_in(client, user)
    assert len(client.get("/api/v1/assets").json()) == 1
    assert client.post("/api/v1/terminal/tickets", json={"asset_id": asset}).status_code == 403


def test_authorized_ticket_bound_to_owner_and_revoked_before_socket(client: TestClient) -> None:
    user = create_user(client)
    asset = create_asset(client, "socket-node")
    assert assign(client, user, [{"asset_id": asset, "can_ssh": True}]).status_code == 200
    sign_in(client, user)
    created = client.post("/api/v1/terminal/tickets", json={"asset_id": asset})
    assert created.status_code == 201
    ticket = created.json()
    assert assign(client, user, []).status_code == 200
    with patch("src.services.terminal_gateway.open_shell") as connect:
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert websocket.receive_json()["type"] == "error"
        connect.assert_not_called()


def test_other_user_cannot_disconnect_or_read_private_terminal(client: TestClient) -> None:
    owner = create_user(client)
    other = create_user(client, "another-user")
    asset = create_asset(client, "private-terminal")
    for user in (owner, other):
        assert assign(client, user, [{"asset_id": asset, "can_ssh": True}]).status_code == 200
    sign_in(client, owner)
    ticket = client.post("/api/v1/terminal/tickets", json={"asset_id": asset}).json()
    sign_in(client, other)
    assert client.get("/api/v1/terminal/sessions").json() == []
    with patch.object(client.app.state.terminal_connections, "terminate") as terminate:
        response = client.post(f"/api/v1/terminal/sessions/{ticket['session_id']}/disconnect")
        assert response.status_code == 403
        terminate.assert_not_called()
    response = client.post("/api/v1/terminal/assistant/analyze", json={
        "session_id": ticket["session_id"], "mode": "analysis_only", "context": "status",
    })
    assert response.status_code == 403
    with patch("src.services.terminal_gateway.open_shell") as connect:
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert websocket.receive_json()["type"] == "error"
        connect.assert_not_called()


def test_active_terminal_ends_after_permission_is_removed(client: TestClient) -> None:
    user = create_user(client)
    asset = create_asset(client, "active-terminal")
    assert assign(client, user, [{"asset_id": asset, "can_ssh": True}]).status_code == 200
    sign_in(client, user)
    ticket = client.post("/api/v1/terminal/tickets", json={"asset_id": asset}).json()
    shell = FakeShell()
    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert websocket.receive_json()["type"] == "connected"
            assert websocket.receive_json()["type"] == "output"
            assert assign(client, user, []).status_code == 200
            assert websocket.receive_json()["type"] == "closed"
    assert shell.active is False


def test_account_audits_redact_secrets_and_protect_last_admin(client: TestClient) -> None:
    admin = create_user(client, "only-admin", "admin")
    user = create_user(client)
    assert client.patch(f"/api/v1/auth/users/{admin['id']}", headers=ADMIN, json={
        "enabled": False,
    }).status_code == 409
    assert client.patch(f"/api/v1/auth/users/{user['id']}", headers=ADMIN, json={
        "role": None,
    }).status_code == 422
    assert client.patch(f"/api/v1/auth/users/{user['id']}", headers=ADMIN, json={
        "role": "auditor",
    }).status_code == 200
    assert client.post(f"/api/v1/auth/users/{user['id']}/reset-password", headers=ADMIN, json={
        "password": "Reset-Only-2026!",
    }).status_code == 200
    audit = client.get("/api/v1/audit-log?source=user_management", headers=ADMIN).json()
    actions = {item["action"] for item in audit["items"]}
    assert {"user_created", "user_updated", "password_reset"} <= actions
    assert "Scope-Test-2026!" not in str(audit)
    assert "Reset-Only-2026!" not in str(audit)
    assert "password_hash" not in str(audit)
