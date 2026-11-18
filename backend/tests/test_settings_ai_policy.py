import json
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.ops_assistant import OpsConversation
from src.models.terminal import TerminalAssistantAnalysis
from src.services.ai_settings import TerminalAIConfig
from src.services.terminal_assistant import chat_reply


def _policy(client: TestClient, **updates: object) -> None:
    current = client.get("/api/v1/system/settings").json()["settings"]["ai_policy"]
    saved = client.put("/api/v1/system/settings/ai_policy", json={**current, **updates})
    assert saved.status_code == 200, saved.text


@pytest.fixture
def terminal(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr("src.services.assets.open_client", Mock())
    asset = client.post("/api/v1/assets", json={
        "name": "ai-policy-node", "host": "192.0.2.55", "ssh_port": 22,
        "os_family": "ubuntu", "environment": "test", "group_name": "policy",
        "labels": [], "ssh_username": "operator",
        "ssh_credential_type": "password", "ssh_secret": "fixture-ssh-password",
    })
    assert asset.status_code == 201, asset.text
    ticket = client.post("/api/v1/terminal/tickets", json={"asset_id": asset.json()["id"]})
    assert ticket.status_code == 201, ticket.text
    return {"session_id": ticket.json()["session_id"], "asset_id": asset.json()["id"]}


@pytest.mark.parametrize("evidence", [
    '{"password":"private-evidence"}',
    '{"nested":{"access_token":"private-evidence"}}',
    '```json\n{"api_key":"private-evidence"}\n```',
    "-----BEGIN PRIVATE KEY-----\nprivate-evidence",
])
def test_terminal_model_redacts_all_input_channels_and_output(evidence: str) -> None:
    configuration = TerminalAIConfig(True, "http://model.internal/v1", "fixture")
    with patch("src.services.terminal_assistant.urlopen") as network:
        network.return_value.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"token":"private-answer"}'}}],
        }).encode()
        reply, _ = chat_reply(
            configuration, '{"password":"private-question"}', evidence,
            [{"role": "assistant", "content": '{"api_key":"private-history"}'}],
        )
    sent = json.loads(network.call_args.args[0].data)
    serialized = json.dumps(sent)
    for secret in ("private-question", "private-evidence", "private-history"):
        assert secret not in serialized
    assert "private-answer" not in reply
    assert "[REDACTED]" in serialized and "[REDACTED]" in reply
    assert "tools" not in sent


@pytest.mark.parametrize("endpoint", ["analyze", "chat"])
def test_terminal_redacts_response_history_and_persisted_evidence(
    client: TestClient, database_session: Session,
    terminal: dict[str, str], endpoint: str,
) -> None:
    payload = {
        "session_id": terminal["session_id"], "mode": "analysis_only",
        "context": '{"nested":{"access_token":"private-evidence"}}',
    }
    if endpoint == "chat":
        payload.update(
            message='{"password":"private-question"}',
            history=[{"role": "assistant", "content": '{"api_key":"private-history"}'}],
        )
    else:
        payload["question"] = '{"password":"private-question"}'
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        '{"token":"private-answer"}', "fixture",
    )) as model:
        response = client.post(f"/api/v1/terminal/assistant/{endpoint}", json=payload)
    assert response.status_code == 201, response.text
    for secret in ("private-question", "private-evidence", "private-history", "private-answer"):
        assert secret not in response.text
    analysis = database_session.scalar(select(TerminalAssistantAnalysis).where(
        TerminalAssistantAnalysis.terminal_session_id == terminal["session_id"],
    ))
    assert analysis is not None
    assert "private-evidence" not in analysis.redacted_context
    assert "[REDACTED]" in analysis.redacted_context
    assert (response.json().get("analysis") or response.json())["commands"] == []
    if endpoint == "chat":
        assert "private-question" not in model.call_args.args[1]
        assert "private-history" not in str(model.call_args.args[3])


@pytest.mark.parametrize("endpoint", [
    "/api/v1/ops-assistant/chat",
    "/api/v1/terminal/assistant/analyze",
    "/api/v1/terminal/assistant/chat",
])
def test_disabling_ops_scene_blocks_every_entry_before_analysis_or_model(
    client: TestClient, database_session: Session,
    terminal: dict[str, str], endpoint: str,
) -> None:
    _policy(client, ops_assistant_enabled=False)
    payload = {"session_id": terminal["session_id"], "mode": "analysis_only",
               "message": "inspect", "question": "inspect"}
    with (
        patch("src.services.terminal_assistant.chat_reply") as model,
        patch("src.services.terminal_assistant.analyze") as analyze,
    ):
        response = client.post(endpoint, json=payload)
    assert response.status_code == 403, response.text
    model.assert_not_called()
    analyze.assert_not_called()
    assert database_session.scalar(select(func.count()).select_from(OpsConversation)) == 0
    assert database_session.scalar(select(func.count()).select_from(TerminalAssistantAnalysis)) == 0


def test_disabling_vulnerability_scene_blocks_triage(client: TestClient) -> None:
    _policy(client, vulnerability_assistant_enabled=False)
    with patch("src.services.ai_agent.create_triage") as triage:
        response = client.post("/api/v1/agent/triage", json={"finding_id": str(uuid4())})
    assert response.status_code == 403, response.text
    triage.assert_not_called()


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_terminal_history_limit_applies_to_model_and_returned_history(
    client: TestClient, terminal: dict[str, str], limit: int,
) -> None:
    _policy(client, history_messages=limit)
    history = [{"role": "assistant", "content": f"earlier-{index}"} for index in range(4)]
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        "read-only answer", "fixture",
    )) as model:
        response = client.post("/api/v1/terminal/assistant/chat", json={
            "session_id": terminal["session_id"], "message": "current", "history": history,
        })
    assert response.status_code == 201, response.text
    assert model.call_args.args[3] == (history[-limit:] if limit else [])
    assert len(response.json()["history"]) == limit


@pytest.mark.parametrize("endpoint", ["analyze", "chat"])
def test_terminal_output_switch_excludes_evidence_from_model_and_storage(
    client: TestClient, database_session: Session,
    terminal: dict[str, str], endpoint: str,
) -> None:
    _policy(client, include_terminal_output=False, history_messages=0)
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        "read-only answer", "fixture",
    )) as model:
        response = client.post(f"/api/v1/terminal/assistant/{endpoint}", json={
            "session_id": terminal["session_id"], "mode": "analysis_only",
            "question": "inspect", "message": "inspect", "context": "excluded-evidence",
        })
    assert response.status_code == 201, response.text
    assert "excluded-evidence" not in response.text
    analysis = database_session.scalar(select(TerminalAssistantAnalysis).where(
        TerminalAssistantAnalysis.terminal_session_id == terminal["session_id"],
    ))
    assert analysis is not None and analysis.redacted_context == ""
    if endpoint == "chat":
        assert model.call_args.args[2] == ""


@pytest.mark.parametrize("asset_context", [False, True])
@pytest.mark.parametrize("terminal_output", [False, True])
def test_ops_context_switches_apply_to_real_conversation(
    client: TestClient, terminal: dict[str, str],
    asset_context: bool, terminal_output: bool,
) -> None:
    _policy(client, include_asset_context=asset_context,
            include_terminal_output=terminal_output, history_messages=0)
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        "read-only answer", "fixture",
    )) as model:
        response = client.post("/api/v1/ops-assistant/chat", json={
            "asset_id": terminal["asset_id"], "context": "attached-evidence",
            "message": "inspect",
        })
    assert response.status_code == 201, response.text
    context = model.call_args.args[2]
    assert ("attached-evidence" in context) is terminal_output
    assert ("ai-policy-node" in context) is asset_context
    assert ("192.0.2.55" in context) is asset_context
    assert model.call_args.args[3] == []
    assert response.json()["read_only"] is True


def test_zero_history_ignores_stored_ops_messages_after_policy_change(client: TestClient) -> None:
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        "first answer", "fixture",
    )):
        first = client.post("/api/v1/ops-assistant/chat", json={"message": "first question"})
    assert first.status_code == 201
    _policy(client, history_messages=0)
    with patch("src.services.terminal_assistant.chat_reply", return_value=(
        "second answer", "fixture",
    )) as model:
        response = client.post("/api/v1/ops-assistant/chat", json={
            "conversation_id": first.json()["conversation_id"], "message": "second question",
        })
    assert response.status_code == 201
    assert model.call_args.args[3] == []
    detail = client.get(f'/api/v1/ops-assistant/conversations/{first.json()["conversation_id"]}')
    assert detail.status_code == 200 and detail.json()["total_messages"] == 4
