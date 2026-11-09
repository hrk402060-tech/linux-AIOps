from fastapi.testclient import TestClient


def test_operations_assistant_supports_generic_read_only_chat(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ops-assistant/chat",
        json={
            "message": "如何排查 Linux 负载升高？",
            "history": [],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["read_only"] is True
    assert body["engine"] == "local-rules（未配置真实模型）"
    assert body["diagnosis"]
    assert body["plan"]
    assert body["validation_steps"]
    assert body["context_sources"] == ["用户问题"]
    assert body["read_only_checks"]
    assert "execution_enabled" not in body


def test_operations_assistant_rejects_unknown_asset(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ops-assistant/chat",
        json={
            "message": "检查服务器状态",
            "asset_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "资产不存在"
