from base64 import b64encode
import hashlib
from unittest.mock import patch

import paramiko
from fastapi.testclient import TestClient
import pytest

from src.services.ssh_host_keys import HostKeyCandidate


def _chat_ticket(client: TestClient) -> dict[str, str]:
    credential = client.post("/api/v1/credentials", json={
        "name": "chat-credential", "credential_type": "password",
        "username": "ops", "secret": "test-secret-value",
    }).json()
    asset_id = client.post("/api/v1/assets", json={
        "name": "chat-node", "host": "10.0.0.52", "ssh_port": 22,
        "os_family": "openeuler", "environment": "test", "group_name": "ops",
        "credential_id": credential["id"], "labels": [],
    }).json()["id"]
    key = paramiko.RSAKey.generate(1024)
    fingerprint = b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    candidate = HostKeyCandidate(
        key.get_name(), f"SHA256:{fingerprint}", f"{key.get_name()} {key.get_base64()}"
    )
    with patch("src.services.ssh_host_keys.probe_host_key", return_value=candidate):
        response = client.post("/api/v1/terminal/tickets", json={"asset_id": asset_id})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("mode", ["analysis_only", "assisted", "controlled"])
def test_chat_route_prioritizes_question_and_redacts_context(client: TestClient, mode: str) -> None:
    ticket = _chat_ticket(client)
    response = client.post("/api/v1/terminal/assistant/chat", json={
        "session_id": ticket["session_id"], "mode": mode,
        "message": "安装 k8s", "context": "sshd active\npassword=hidden-value",
        "history": [],
    })

    assert response.status_code == 201
    body = response.json()
    assert body["engine"] == "local-rules（未配置真实模型）"
    assert "Kubernetes" in body["reply"]
    assert "hidden-value" not in response.text
    assert body["analysis"]["mode"] == "analysis_only"
    assert body["analysis"]["commands"] == []


def test_chat_route_rejects_closed_session(client: TestClient) -> None:
    ticket = _chat_ticket(client)
    with client.app.state.session_factory() as session:
        from src.models.terminal import TerminalSession
        item = session.get(TerminalSession, ticket["session_id"])
        assert item is not None
        item.status = "closed"
        session.commit()

    response = client.post("/api/v1/terminal/assistant/chat", json={
        "session_id": ticket["session_id"], "message": "检查服务", "history": [],
    })
    assert response.status_code == 409
