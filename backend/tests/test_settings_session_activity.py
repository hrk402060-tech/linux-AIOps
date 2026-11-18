from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Any

from fastapi.testclient import TestClient
import pytest

from src.auth import AuthPrincipal
from src.models.auth import AuthSession, User
from src.routes import terminal
from src.services import auth, terminal_lifecycle
from test_terminal_api import FakeShell


@dataclass(frozen=True)
class SessionCase:
    session_id: str
    user_id: str
    token: str
    last_used_at: datetime
    expires_at: datetime


def create_session(
    client: TestClient, *, idle_age: int = 290, created_age: int = 600,
    revoked: bool = False, expiry_seconds: int = 600, save_policy: bool = True,
) -> SessionCase:
    if save_policy:
        policy = client.get("/api/v1/system/auth-policy").json()
        payload = {key: value for key, value in policy.items()
                   if key not in {"source", "updated_at", "updated_by"}}
        payload.update(session_idle_timeout_seconds=300, session_ttl_seconds=900)
        assert client.put("/api/v1/system/auth-policy", json=payload).status_code == 200
    now = datetime.now(timezone.utc)
    token = "activity-session-test-token"
    with client.app.state.session_factory() as session:
        user = User(
            username="activity-user", password_hash="unusable-test-password-hash",
            role="admin", asset_scope="all",
        )
        session.add(user)
        session.flush()
        item = AuthSession(
            user_id=user.id, token_hash=auth.hash_token(token),
            created_at=now - timedelta(seconds=created_age),
            last_used_at=now - timedelta(seconds=idle_age),
            expires_at=now + timedelta(seconds=expiry_seconds),
            revoked_at=now if revoked else None,
        )
        session.add(item)
        session.commit()
        return SessionCase(
            item.id, user.id, token,
            auth._as_utc(item.last_used_at), auth._as_utc(item.expires_at),
        )


def last_used(client: TestClient, case: SessionCase) -> datetime:
    with client.app.state.session_factory() as session:
        item = session.get(AuthSession, case.session_id)
        assert item is not None
        assert auth._as_utc(item.expires_at) == case.expires_at
        return auth._as_utc(item.last_used_at)


def test_http_activity_renews_the_minimum_idle_window(client: TestClient) -> None:
    case = create_session(client)
    client.headers.pop("Authorization")
    client.cookies.set(client.app.state.runtime_settings.auth_cookie_name, case.token)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    assert last_used(client, case) > case.last_used_at


@pytest.mark.parametrize("updates", [
    {"idle_age": 301}, {"revoked": True}, {"created_age": 901}, {"expiry_seconds": -1},
])
def test_activity_cannot_revive_an_invalid_session(
    client: TestClient, updates: dict[str, Any],
) -> None:
    case = create_session(client, **updates)
    with client.app.state.session_factory() as session:
        item = session.get(AuthSession, case.session_id)
        assert item is not None
        assert auth.touch_session_activity(
            session, item, client.app.state.runtime_settings,
        ) is False
    assert last_used(client, case) == case.last_used_at


class ActivitySocket:
    def __init__(
        self, client: TestClient, shell: FakeShell, message: dict[str, object],
    ) -> None:
        self.app = client.app
        self.shell = shell
        self.message = message

    async def send_json(self, value: dict[str, object]) -> None:
        return None

    async def receive_json(self) -> dict[str, object]:
        self.shell.active = False
        return self.message


@pytest.mark.parametrize(("message", "renews"), [
    ({"type": "input", "data": "whoami\n"}, True),
    ({"type": "input", "data": ""}, False),
    ({"type": "resize", "columns": 100, "rows": 30}, False),
    ({"type": "ping"}, False),
])
def test_terminal_only_successful_nonempty_input_renews_login_activity(
    client: TestClient, message: dict[str, object], renews: bool,
) -> None:
    case = create_session(client)
    shell = FakeShell()
    socket = ActivitySocket(client, shell, message)
    context = terminal.TerminalAuthContext(
        AuthPrincipal("activity-user", "admin", case.user_id, "session"), case.session_id,
    )
    with client.app.state.session_factory() as session:
        result = asyncio.run(terminal._bridge(
            socket, shell, Event(), terminal.TerminalTraffic(), session, "terminal-test",
            client.app.state.session_factory, context, "test-asset",
            terminal_lifecycle.TerminalOutcome(),
        ))
    assert result == "closed"
    assert (last_used(client, case) > case.last_used_at) is renews


def test_terminal_session_policy_uses_its_app_runtime_defaults(client: TestClient) -> None:
    client.app.state.runtime_settings.auth_session_ttl_seconds = 900
    case = create_session(client, created_age=901, idle_age=1, save_policy=False)
    context = terminal.TerminalAuthContext(
        AuthPrincipal("activity-user", "admin", case.user_id, "session"), case.session_id,
    )
    with client.app.state.session_factory() as session:
        with pytest.raises(ValueError, match="失效"):
            terminal._require_socket_access(
                session, context, "test-asset", client.app.state.runtime_settings,
            )
