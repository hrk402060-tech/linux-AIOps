import json
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from src.services.terminal_assistant import TerminalAssistantError

from src.services.ai_settings import TerminalAIConfig
from src.services.terminal_assistant import chat_reply


def test_local_chat_reply_prioritizes_question_and_redacts_context() -> None:
    settings = TerminalAIConfig()

    reply, engine = chat_reply(
        settings,
        "安装 k8s",
        "sshd active\npassword=hidden-value",
        [],
    )

    assert engine == "local-rules（未配置真实模型）"
    assert "Kubernetes" in reply
    assert "hidden-value" not in reply


def test_remote_model_is_strictly_read_only_without_tool_dispatch() -> None:
    settings = TerminalAIConfig(
        enabled=True, base_url="http://model.internal/v1", model="test-model",
    )
    with patch("src.services.terminal_assistant.urlopen") as invoke:
        invoke.return_value.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": "建议管理员人工检查服务状态。"}}],
        }).encode()
        reply, engine = chat_reply(settings, "重启服务", "token=hidden-value", [])
    request = invoke.call_args.args[0]
    payload = json.loads(request.data)
    system = payload["messages"][0]["content"]
    assert "严格只读" in system
    assert "没有任何删除、修改" in system
    assert "tools" not in payload
    assert "hidden-value" not in request.data.decode()
    assert engine == "openai-compatible:test-model"
    assert "人工" in reply


@pytest.mark.parametrize('error', [TimeoutError(), URLError('private upstream error'),
    HTTPError('http://private-model', 401, 'secret', {}, None)])
def test_remote_errors_sanitized_without_retry(error: Exception) -> None:
    config = TerminalAIConfig(True, 'http://model.internal/v1', 'ops', 'private-key', 7)
    with patch('src.services.terminal_assistant.urlopen', side_effect=error) as network:
        with pytest.raises(TerminalAssistantError) as caught:
            chat_reply(config, '检查服务', 'password=secret', [])
    assert network.call_count == 1 and network.call_args.kwargs['timeout'] == 7
    assert 'private' not in str(caught.value) and 'secret' not in str(caught.value)


@pytest.mark.parametrize('content', [None, {}, '', 'a' * 4001])
def test_invalid_model_response_is_rejected(content: object) -> None:
    config = TerminalAIConfig(True, 'http://model.internal/v1', 'ops')
    with patch('src.services.terminal_assistant.urlopen') as network:
        network.return_value.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': content}}],
        }).encode()
        with pytest.raises(TerminalAssistantError):
            chat_reply(config, '检查', '', [])
