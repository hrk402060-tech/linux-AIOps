import base64
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
import paramiko
import pytest
from paramiko.ssh_exception import NoValidConnectionsError
from starlette.websockets import WebSocketDisconnect
from src.routes.terminal import _ssh_failure_message
from src.models.ssh_host_key import SshHostKey
from src.models.terminal import TerminalSession
from src.services.ssh_host_keys import HostKeyCandidate


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

    def resize(self, columns: int, rows: int) -> None:
        assert columns == 100
        assert rows == 30

    def close(self) -> None:
        self.active = False


def _asset(client: TestClient, register_host_key: bool = True) -> str:
    credential = client.post("/api/v1/credentials", json={
        "name": "Terminal operator", "credential_type": "password",
        "username": "ops", "secret": "never-return-terminal-secret",
    }).json()
    asset_id = client.post("/api/v1/assets", json={
        "name": "terminal-node-01", "host": "10.0.0.31", "ssh_port": 22,
        "os_family": "openeuler", "environment": "production",
        "group_name": "核心业务", "credential_id": credential["id"], "labels": [],
    }).json()["id"]
    if register_host_key:
        _register_host_key(client, asset_id)
    return asset_id


def _register_host_key(client: TestClient, asset_id: str) -> HostKeyCandidate:
    key = paramiko.RSAKey.generate(1024)
    digest = (
        base64.b64encode(hashlib.sha256(key.asbytes()).digest())
        .decode("ascii")
        .rstrip("=")
    )
    candidate = HostKeyCandidate(
        key_type=key.get_name(),
        fingerprint=f"SHA256:{digest}",
        public_key=f"{key.get_name()} {key.get_base64()}",
    )
    with client.app.state.session_factory() as session:
        session.add(SshHostKey(
            id=str(uuid4()), asset_id=asset_id, host="10.0.0.31", port=22,
            key_type=candidate.key_type, fingerprint=candidate.fingerprint,
            public_key=candidate.public_key, confirmed_by="test-admin",
        ))
        session.commit()
    return candidate


def test_ticket_allows_connection_without_manual_host_key_confirmation(client: TestClient) -> None:
    asset_id = _asset(client, register_host_key=False)
    _register_host_key(client, asset_id)
    response = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    })

    assert response.status_code == 201


def test_address_change_does_not_require_host_key_reconfirmation(client: TestClient) -> None:
    asset_id = _asset(client)
    changed = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"host": "10.0.0.99", "ssh_port": 2222},
    )
    assert changed.status_code == 200
    accepted = client.post(
        "/api/v1/terminal/tickets", json={"asset_id": asset_id}
    )
    assert accepted.status_code == 201


def test_ticket_and_session_audit_never_expose_credentials(client: TestClient) -> None:
    asset_id = _asset(client)
    response = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    })

    assert response.status_code == 201
    body = response.json()
    assert len(body["ticket"]) >= 32
    assert body["websocket_path"] == "/api/v1/terminal/ws"
    assert "never-return-terminal-secret" not in response.text
    sessions = client.get("/api/v1/terminal/sessions").json()
    assert sessions[0]["asset_id"] == asset_id
    assert sessions[0]["status"] == "waiting"
    assert sessions[0]["ticket_expires_at"]
    assert "credential" not in str(sessions[0]).lower()


def test_websocket_consumes_ticket_once_and_closes_session(client: TestClient) -> None:
    asset_id = _asset(client)
    ticket = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    }).json()["ticket"]
    shell = FakeShell()

    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({
                "type": "auth", "ticket": ticket, "columns": 100, "rows": 30,
            })
            assert websocket.receive_json()["type"] == "connected"
            assert websocket.receive_json() == {"type": "output", "data": "ready\r\n"}
            websocket.send_json({"type": "input", "data": "whoami\n"})
            websocket.send_json({"type": "resize", "columns": 100, "rows": 30})

    assert shell.sent == ["whoami\n"]
    with patch("src.services.terminal_gateway.open_shell", return_value=FakeShell()):
        try:
            with client.websocket_connect(
                "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
            ) as websocket:
                websocket.send_json({
                    "type": "auth", "ticket": ticket, "columns": 80, "rows": 24,
                })
                websocket.receive_json()
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_ticket_requires_bound_credential(client: TestClient) -> None:
    asset_id = client.post("/api/v1/assets", json={
        "name": "no-terminal-credential", "host": "10.0.0.32", "ssh_port": 22,
        "os_family": "ubuntu", "environment": "test",
        "group_name": "测试", "credential_id": None, "labels": [],
    }).json()["id"]
    response = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    })
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (paramiko.AuthenticationException(), "SSH 登录认证失败"),
        (NoValidConnectionsError({("10.0.0.32", 22): OSError("refused")}), "SSH 端口无法连接"),
        (TimeoutError(), "SSH 连接超时"),
    ],
)
def test_terminal_failure_message_is_actionable(error: Exception, message: str) -> None:
    assert message in _ssh_failure_message(error)


def test_websocket_rejects_untrusted_origin(client: TestClient) -> None:
    asset_id = _asset(client)
    ticket = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    }).json()["ticket"]
    try:
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "https://attacker.example"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket})
            websocket.receive_json()
    except WebSocketDisconnect as exc:
        assert exc.code == 4403


def test_expired_waiting_ticket_is_marked_failed(
    client: TestClient, database_session
) -> None:
    asset_id = _asset(client)
    created = client.post("/api/v1/terminal/tickets", json={
        "asset_id": asset_id, "operator": "admin",
    }).json()
    item = database_session.get(TerminalSession, created["session_id"])
    item.ticket_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    database_session.commit()

    audit = client.get("/api/v1/terminal/sessions").json()[0]
    assert audit["status"] == "failed"
    assert audit["failure_reason"] == "终端票据已过期"


@pytest.mark.parametrize("reason", ["terminated", "closed", "timeout"])
def test_server_ends_websocket_and_releases_session(client: TestClient, reason: str) -> None:
    asset_id = _asset(client)
    ticket = client.post("/api/v1/terminal/tickets", json={"asset_id": asset_id}).json()
    shell = FakeShell()
    if reason == "closed":
        shell.active = False
    if reason == "timeout":
        client.app.state.terminal_idle_timeout = 0
    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert websocket.receive_json()["type"] == "connected"
            if reason == "terminated":
                assert websocket.receive_json()["type"] == "output"
                response = client.post(f"/api/v1/terminal/sessions/{ticket['session_id']}/disconnect")
                assert response.status_code == 202
            ended = websocket.receive_json()
            assert ended["type"] == "closed" and ended["reason"] == reason
            assert ended["message"] and ended["ended_at"]
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1000
    assert not shell.active
    assert not client.app.state.terminal_connections.terminate(ticket["session_id"])
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, ticket["session_id"])
        assert item.status == reason
        assert item.ended_at is not None and item.failure_reason
