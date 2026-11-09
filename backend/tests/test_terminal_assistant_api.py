import base64
from collections import deque
import hashlib
from unittest.mock import patch

from fastapi.testclient import TestClient
import paramiko
import pytest
from sqlalchemy.orm import Session
from uuid import uuid4

from src.models.terminal import TerminalAssistantAnalysis, TerminalCommandAudit
from src.services import terminal_assistant

from src.services.ssh_host_keys import HostKeyCandidate


class FakeShell:
    def __init__(self) -> None:
        self.output: deque[bytes] = deque()
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
        return None

    def close(self) -> None:
        self.active = False


def _asset(client: TestClient) -> str:
    credential = client.post("/api/v1/credentials", json={
        "name": "assistant-credential", "credential_type": "password",
        "username": "ops", "secret": "never-return-assistant-secret",
    }).json()
    return client.post("/api/v1/assets", json={
        "name": "assistant-node", "host": "10.0.0.51", "ssh_port": 22,
        "os_family": "openeuler", "environment": "test",
        "group_name": "运维", "credential_id": credential["id"], "labels": [],
    }).json()["id"]


def _ticket(client: TestClient) -> dict[str, str]:
    asset_id = _asset(client)
    key = paramiko.RSAKey.generate(1024)
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
    candidate = HostKeyCandidate(
        key.get_name(), f"SHA256:{digest}", f"{key.get_name()} {key.get_base64()}"
    )
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=candidate):
        response = client.post("/api/v1/terminal/tickets", json={
            "asset_id": asset_id, "operator": "admin",
        })
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("mode", ["analysis_only", "assisted", "controlled"])
def test_analysis_redacts_secrets_and_enforces_read_only(client: TestClient, mode: str) -> None:
    ticket = _ticket(client)
    response = client.post("/api/v1/terminal/assistant/analyze", json={
        "session_id": ticket["session_id"], "mode": mode,
        "context": "No space left on device\nAPI_TOKEN=top-secret-value\npassword: hidden-value",
        "operator": "admin",
    })

    assert response.status_code == 201
    body = response.json()
    assert body["mode"] == "analysis_only"
    assert body["commands"] == []
    assert "top-secret-value" not in response.text
    assert "hidden-value" not in response.text
    assert "[REDACTED]" in body["redacted_context"]


def test_analysis_only_never_returns_executable_commands(client: TestClient) -> None:
    ticket = _ticket(client)
    response = client.post("/api/v1/terminal/assistant/analyze", json={
        "session_id": ticket["session_id"], "mode": "analysis_only",
        "context": "systemd service failed", "operator": "admin",
    })
    assert response.status_code == 201
    assert response.json()["commands"] == []


def test_chat_keeps_question_authoritative_and_reports_local_engine(client: TestClient) -> None:
    ticket = _ticket(client)
    response = client.post("/api/v1/terminal/assistant/chat", json={
        "session_id": ticket["session_id"], "mode": "analysis_only",
        "message": "安装 k8s", "context": "sshd active\npassword=hidden",
        "history": [],
    })

    assert response.status_code == 201
    body = response.json()
    assert body["engine"] == "local-rules（未配置真实模型）"
    assert "Kubernetes" in body["reply"]
    assert "password=hidden" not in response.text
    assert body["analysis"]["commands"] == []


def _historical_command(session: Session, session_id: str, status: str) -> TerminalCommandAudit:
    analysis = TerminalAssistantAnalysis(
        id=str(uuid4()), terminal_session_id=session_id, mode="controlled",
        redacted_context="old evidence", diagnosis="old diagnosis", plan=[],
        validation_steps=[], operator="historical-admin",
    )
    item = TerminalCommandAudit(
        id=str(uuid4()), analysis_id=analysis.id, terminal_session_id=session_id,
        command="systemctl restart app", purpose="old suggestion", validation_step="check",
        risk_level="medium", status=status, confirmation_phrase="EXECUTE old",
        requested_by="historical-admin",
    )
    session.add_all([analysis, item])
    session.commit()
    return item


@pytest.mark.parametrize("confirmation", ["wrong", "  EXECUTE old  "])
def test_historical_command_approval_is_forbidden(
    client: TestClient, database_session: Session, confirmation: str,
) -> None:
    ticket = _ticket(client)
    item = _historical_command(database_session, ticket["session_id"], "pending_confirmation")
    response = client.patch(
        f"/api/v1/terminal/assistant/commands/{item.id}/review",
        json={"decision": "approved", "confirmation": confirmation},
    )
    assert response.status_code == 403
    database_session.refresh(item)
    assert item.status == "pending_confirmation"
    assert item.reviewed_at is None


@pytest.mark.parametrize("command_status", ["approved", "pending_confirmation", "executed"])
def test_historical_command_cannot_begin_execution(
    client: TestClient, database_session: Session, command_status: str,
) -> None:
    ticket = _ticket(client)
    item = _historical_command(database_session, ticket["session_id"], command_status)
    with pytest.raises(terminal_assistant.TerminalAssistantReadOnlyError):
        terminal_assistant.begin_execution(database_session, ticket["session_id"], item.id)
    database_session.refresh(item)
    assert item.status == command_status
    with pytest.raises(terminal_assistant.TerminalAssistantReadOnlyError):
        terminal_assistant.finish_execution(database_session, item, True)
    assert item.executed_at is None


def test_historical_pending_command_can_only_be_rejected(
    client: TestClient, database_session: Session,
) -> None:
    ticket = _ticket(client)
    item = _historical_command(database_session, ticket["session_id"], "pending_confirmation")
    response = client.patch(
        f"/api/v1/terminal/assistant/commands/{item.id}/review",
        json={"decision": "rejected", "confirmation": "  EXECUTE old  "},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["reviewed_by"] == "break-glass-admin"


def test_websocket_rejects_ai_command_and_preserves_manual_input(
    client: TestClient, database_session: Session,
) -> None:
    ticket = _ticket(client)
    item = _historical_command(database_session, ticket["session_id"], "approved")
    shell = FakeShell()
    with patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with client.websocket_connect(
            "/api/v1/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as websocket:
            websocket.send_json({"type": "auth", "ticket": ticket["ticket"]})
            assert websocket.receive_json()["type"] == "connected"
            websocket.send_json({"type": "approved_command", "command_id": item.id})
            assert websocket.receive_json()["type"] == "error"
            assert shell.sent == []
            websocket.send_json({"type": "input", "data": "whoami\n"})
            assert websocket.receive_json()["type"] == "closed"
    assert shell.sent == ["whoami\n"]
    database_session.refresh(item)
    assert item.status == "approved"
