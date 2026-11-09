from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(("method", "path"), [
    ("DELETE", "/api/v1/vulnerabilities/missing?purpose=test_data_cleanup&reason=test"),
    ("DELETE", "/api/v1/assets/missing"),
    ("POST", "/api/v1/assets/missing/cleanup"),
    ("POST", "/api/v1/knowledge/imports"),
    ("POST", "/api/v1/knowledge/sources/missing/sync"),
    ("POST", "/api/v1/knowledge/releases/missing/rollback"),
    ("POST", "/api/v1/knowledge/backfills"),
    ("POST", "/api/v1/knowledge/backfills/missing/resume"),
    ("POST", "/api/v1/knowledge/backfills/missing/cancel"),
    ("POST", "/api/v1/agent/providers/config"),
    ("PATCH", "/api/v1/agent/providers/config/deepseek/enabled"),
    ("POST", "/api/v1/agent/providers/config/deepseek/validate"),
])
def test_operator_cannot_access_admin_writes(client: TestClient, method: str, path: str) -> None:
    client.headers.pop("Authorization", None)
    client.cookies.set(client.app.state.runtime_settings.auth_cookie_name, "review-session")
    user = SimpleNamespace(username="operator", role="operator", id="operator-id", must_change_password=False)
    with patch("src.auth.get_session_user", return_value=(None, user)):
        assert client.request(method, path, json={}).status_code == 403
