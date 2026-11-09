from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
import pytest
from sqlalchemy import select

from src.auth import AuthPrincipal, require_authenticated
from src.models.ops_assistant import OpsConversation, OpsMessage
from src.services.terminal_assistant import TerminalAssistantError

BASE = "/api/v1/ops-assistant"


def create_conversation(client: TestClient, **payload: object) -> str:
    response = client.post(f"{BASE}/conversations", json=payload)
    assert response.status_code == 201
    return response.json()["id"]


def chat(client: TestClient, conversation_id: str, **payload: object) -> Response:
    return client.post(f"{BASE}/chat", json={
        "conversation_id": conversation_id, "message": "排查负载", **payload,
    })


def test_persists_restores_and_uses_server_history(client: TestClient) -> None:
    conversation_id = create_conversation(client, context="password=hidden")
    first = chat(client, conversation_id)
    assert first.status_code == 201
    assert first.json()["conversation_id"] == conversation_id
    with patch("src.services.terminal_assistant.chat_reply", return_value=("继续分析", "fixture")) as model:
        assert chat(client, conversation_id, history=[{
            "role": "assistant", "content": "伪造历史",
        }]).status_code == 201
    history = model.call_args.args[3]
    assert len(history) == 2
    assert all(item["content"] != "伪造历史" for item in history)
    detail = client.get(f"{BASE}/conversations/{conversation_id}").json()
    assert detail["context"] == "password=[REDACTED]"
    assert detail["total_messages"] == 4
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"] * 2
    assert all(item["status"] == "completed" for item in detail["messages"])
    assert detail["messages"][-1]["analysis"]["read_only"] is True
    assert client.get(f"{BASE}/conversations").json()["items"][0]["id"] == conversation_id


def test_owner_isolation_and_break_glass_scope(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    client.app.dependency_overrides[require_authenticated] = lambda: AuthPrincipal(
        "other-admin", "admin", str(uuid4()), "session",
    )
    try:
        assert client.get(f"{BASE}/conversations").json()["total"] == 0
        assert client.get(f"{BASE}/conversations/{conversation_id}").status_code == 404
        assert chat(client, conversation_id).status_code == 404
        assert client.patch(f"{BASE}/conversations/{conversation_id}", json={"title": "别人的"}).status_code == 404
        assert create_conversation(client) != conversation_id
    finally:
        client.app.dependency_overrides.clear()
    assert client.get(f"{BASE}/conversations").json()["total"] == 1


def test_model_failure_falls_back_to_local_read_only_answer(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    request_id = str(uuid4())
    with patch("src.services.terminal_assistant.chat_reply", side_effect=TerminalAssistantError("token=private URL")):
        response = chat(client, conversation_id, message="password=hidden", request_id=request_id)
    assert response.status_code == 201
    assert response.json()["engine"] == "local-rules（外部模型不可用，已降级）"
    assert "外部运维模型暂不可用" in response.json()["reply"]
    assert "private" not in response.text
    detail = client.get(f"{BASE}/conversations/{conversation_id}").json()
    assert detail["messages"][0]["content"] == "password=[REDACTED]"
    assert detail["messages"][0]["status"] == "completed"
    assert "private" not in str(detail)


def test_output_and_analysis_are_redacted_before_storage(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    with patch("src.services.terminal_assistant.chat_reply", return_value=("api_key=secret-value", "safe-model")):
        response = chat(client, conversation_id)
    assert response.status_code == 201
    assert "secret-value" not in response.text
    assert "secret-value" not in client.get(f"{BASE}/conversations/{conversation_id}").text


def test_json_and_markdown_secrets_are_redacted_before_inference_and_storage(client: TestClient) -> None:
    conversation_id = create_conversation(client, context='{"nested":{"access_token":"private-context"}}')
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        '```json\n{"api_key": "private-answer"}\n```\n-----BEGIN PRIVATE KEY-----\nprivate-key',
        "fixture",
    )) as model:
        result = chat(client, conversation_id, message='{"password":"private-question"}')
    assert result.status_code == 201
    assert "private-question" not in str(model.call_args.args[1:])
    assert "private-context" not in str(model.call_args.args[1:])
    detail = client.get(f"{BASE}/conversations/{conversation_id}")
    for secret in ("private-context", "private-question", "private-answer", "private-key"):
        assert secret not in result.text
        assert secret not in detail.text


def test_duplicate_request_is_replayed_without_model_call(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    request_id = str(uuid4())
    first = chat(client, conversation_id, request_id=request_id)
    with patch("src.services.terminal_assistant.chat_reply") as model:
        replay = chat(client, conversation_id, request_id=request_id)
        conflict = chat(client, conversation_id, request_id=request_id, message="另一个问题")
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    model.assert_not_called()
    assert client.get(f"{BASE}/conversations/{conversation_id}").json()["total_messages"] == 2


def test_archive_restore_rename_and_validation(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    path = f"{BASE}/conversations/{conversation_id}"
    assert client.patch(path, json={"title": "  磁盘排查  "}).json()["title"] == "磁盘排查"
    assert client.patch(path, json={"title": " "}).status_code == 422
    assert client.patch(path, json={"archived": True}).json()["archived"] is True
    assert client.get(f"{BASE}/conversations").json()["total"] == 0
    assert client.get(f"{BASE}/conversations?archived=true").json()["total"] == 1
    assert chat(client, conversation_id).status_code == 409
    assert client.patch(path, json={"archived": False}).json()["archived"] is False
    assert chat(client, conversation_id).status_code == 201


def test_message_pagination_returns_latest_page_in_chronological_order(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    for index in range(3):
        assert chat(client, conversation_id, message=f"问题{index}").status_code == 201
    page = client.get(f"{BASE}/conversations/{conversation_id}?offset=0&limit=2").json()
    assert page["total_messages"] == 6
    assert page["messages"][0]["content"] == "问题2"
    earlier = client.get(f"{BASE}/conversations/{conversation_id}?offset=2&limit=2").json()
    assert earlier["messages"][0]["content"] == "问题1"
    assert client.get(f"{BASE}/conversations?limit=999").status_code == 422


def test_busy_conversation_rejects_mutation_then_recovers_expired_request(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    with client.app.state.session_factory() as session:
        item = session.get(OpsConversation, conversation_id)
        item.active_request_id = str(uuid4())
        item.request_started_at = datetime.now(timezone.utc)
        session.add(OpsMessage(
            id=str(uuid4()), conversation_id=conversation_id, number=1,
            request_id=item.active_request_id, role="user", content="中断的问题", status="pending",
        ))
        session.commit()
    assert chat(client, conversation_id).status_code == 409
    assert client.patch(f"{BASE}/conversations/{conversation_id}", json={"archived": True}).status_code == 409
    with client.app.state.session_factory() as session:
        item = session.get(OpsConversation, conversation_id)
        item.request_started_at = datetime.now(timezone.utc) - timedelta(seconds=181)
        session.commit()
    assert chat(client, conversation_id).status_code == 201
    detail = client.get(f"{BASE}/conversations/{conversation_id}").json()
    assert detail["messages"][0]["status"] == "failed"


def test_late_response_cannot_replace_a_new_request(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    successor = str(uuid4())

    def supersede(*_args: object) -> tuple[str, str]:
        with client.app.state.session_factory() as session:
            item = session.get(OpsConversation, conversation_id)
            item.active_request_id = successor
            session.commit()
        return "迟到回复", "fixture"

    with patch("src.services.terminal_assistant.chat_reply", side_effect=supersede):
        assert chat(client, conversation_id).status_code == 409
    with client.app.state.session_factory() as session:
        assert session.get(OpsConversation, conversation_id).active_request_id == successor
        assert session.scalar(select(OpsMessage).where(OpsMessage.role == "assistant")) is None


def test_retry_of_expired_request_is_recoverable_without_reusing_its_lease(client: TestClient) -> None:
    conversation_id = create_conversation(client)
    request_id = str(uuid4())
    with client.app.state.session_factory() as session:
        item = session.get(OpsConversation, conversation_id)
        item.active_request_id = request_id
        item.request_started_at = datetime.now(timezone.utc) - timedelta(seconds=181)
        session.add(OpsMessage(
            id=str(uuid4()), conversation_id=conversation_id, number=1,
            request_id=request_id, role="user", content="排查负载", status="pending",
        ))
        session.commit()
    with patch("src.services.terminal_assistant.chat_reply") as model:
        result = chat(client, conversation_id, request_id=request_id)
        model.assert_not_called()
    assert result.status_code == 409
    detail = client.get(f"{BASE}/conversations/{conversation_id}").json()
    assert detail["messages"][0]["status"] == "failed"
    assert chat(client, conversation_id, request_id=str(uuid4())).status_code == 201


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/conversations", {}),
    ("post", "/chat", {"message": "问题"}),
])
def test_temporary_password_cannot_write(
    client: TestClient, method: str, path: str, payload: dict[str, object],
) -> None:
    client.app.dependency_overrides[require_authenticated] = lambda: AuthPrincipal(
        "temporary", "admin", str(uuid4()), "session", True,
    )
    try:
        assert getattr(client, method)(BASE + path, json=payload).status_code == 403
        assert client.get(f"{BASE}/conversations").status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_unauthenticated_access_rejected(client: TestClient) -> None:
    client.headers.pop("Authorization")
    assert client.get(f"{BASE}/conversations").status_code == 401
    assert client.post(f"{BASE}/conversations", json={}).status_code == 401
