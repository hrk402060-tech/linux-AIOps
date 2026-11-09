from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.models.terminal import TerminalSession
from test_background_asset_authorization import change_authorization
from test_terminal_api import FakeShell
from test_user_asset_access import ADMIN, assign, create_asset, create_user, sign_in


def prepare_terminal(client: TestClient) -> tuple[dict, str, dict]:
    user = create_user(client)
    asset_id = create_asset(client, "runtime-terminal")
    assert assign(client, user, [{"asset_id": asset_id, "can_ssh": True}]).status_code == 200
    sign_in(client, user)
    response = client.post("/api/v1/terminal/tickets", json={"asset_id": asset_id})
    assert response.status_code == 201
    return user, asset_id, response.json()


@pytest.mark.parametrize("change", ["revoke", "role", "password", "ssh", "asset"])
def test_ticket_rechecks_authorization_without_local_registry(
    client: TestClient, change: str,
) -> None:
    user, asset_id, ticket = prepare_terminal(client)
    if change == "ssh":
        from src.models.asset_access import UserAssetAccess
        from sqlalchemy import update
        with client.app.state.session_factory() as session:
            session.execute(update(UserAssetAccess).where(
                UserAssetAccess.user_id == user["id"],
            ).values(can_ssh=False))
            session.commit()
    elif change == "asset":
        from src.models.asset import Asset, AssetLifecycleStatus
        with client.app.state.session_factory() as session:
            session.get(Asset, asset_id).lifecycle_status = AssetLifecycleStatus.DISABLED
            session.commit()
    else:
        change_authorization(client, user["id"], change)
    with patch("src.services.terminal_gateway.open_shell") as connect:
        with client.websocket_connect("/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert ws.receive_json()["type"] == "error"
        connect.assert_not_called()


def test_live_socket_rechecks_before_forwarding_input_without_registry(client: TestClient) -> None:
    user, _, ticket = prepare_terminal(client)
    shell = FakeShell()
    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect("/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert ws.receive_json()["type"] == "connected"
            assert ws.receive_json()["type"] == "output"
            change_authorization(client, user["id"], "revoke")
            ws.send_json({"type": "input", "data": "should-not-be-sent\n"})
            assert ws.receive_json()["type"] == "error"
            ended = ws.receive_json()
            assert ended["type"] == "closed" and ended["reason"] == "revoked"
            assert ended["ended_at"] and ended["message"]
    assert shell.sent == []
    assert not shell.active
    with client.app.state.session_factory() as session:
        assert session.get(TerminalSession, ticket["session_id"]).status == "revoked"


def test_open_race_closes_new_shell_before_marking_connected(client: TestClient) -> None:
    user, _, ticket = prepare_terminal(client)
    shell = FakeShell()

    def open_then_revoke(*args: object) -> FakeShell:
        del args
        change_authorization(client, user["id"], "disable")
        return shell

    with patch("src.services.terminal_gateway.open_shell", side_effect=open_then_revoke):
        with client.websocket_connect("/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert ws.receive_json()["type"] == "error"
    assert not shell.active
    with client.app.state.session_factory() as session:
        assert session.get(TerminalSession, ticket["session_id"]).connected_at is None


def test_revoking_current_browser_session_stops_its_connected_socket(client: TestClient) -> None:
    user, _, ticket = prepare_terminal(client)
    sessions = client.get(f"/api/v1/auth/sessions?user_id={user['id']}", headers=ADMIN).json()
    shell = FakeShell()
    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect("/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
            ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert ws.receive_json()["type"] == "connected"
            assert ws.receive_json()["type"] == "output"
            assert client.post(
                f"/api/v1/auth/users/{user['id']}/sessions/{sessions[0]['id']}/revoke", headers=ADMIN,
            ).status_code == 204
            ws.send_json({"type": "input", "data": "id\n"})
            assert ws.receive_json()["type"] == "error"
    assert shell.sent == []
