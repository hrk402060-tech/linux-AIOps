import asyncio
from unittest.mock import patch

import anyio
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.terminal import TerminalSession
from src.routes import terminal
from src.services import terminal_sessions
from test_terminal_api import FakeShell
from test_terminal_lifecycle import LifecycleSocket, assert_finished, patches
import test_terminal_session_finalization as finalization

terminal_id = finalization.terminal_id


def test_connection_commit_failure_closes_shell_and_seals_with_fresh_transaction(
    client: TestClient, terminal_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    shell, socket, rejected = FakeShell(), LifecycleSocket(client), False
    original_commit = Session.commit

    def fail_first_commit(session: Session) -> None:
        nonlocal rejected
        if not rejected:
            rejected = True
            raise SQLAlchemyError("private-driver-detail")
        original_commit(session)

    auth_patch, _ = patches(client, terminal_id, shell)
    with auth_patch, patch("src.services.terminal_gateway.open_shell", return_value=shell):
        with patch.object(Session, "commit", new=fail_first_commit):
            asyncio.run(terminal.terminal_socket(socket))
    item = assert_finished(client, terminal_id, "failed")
    assert rejected and item.connected_at is None and not shell.active
    assert item.failure_reason == "SSH 连接状态保存失败"
    assert not any(message["type"] == "connected" for message in socket.messages)
    assert "private-driver-detail" not in caplog.text + str(socket.messages)


def test_finalizer_database_outage_closes_shell_without_claiming_saved_state(
    client: TestClient, terminal_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    shell, socket, unavailable = FakeShell(), LifecycleSocket(client), False
    original_get = Session.get

    def get(session: Session, model: type, identity: str, **kwargs: object) -> object:
        if unavailable and model is TerminalSession:
            raise SQLAlchemyError("private-driver-detail")
        return original_get(session, model, identity, **kwargs)

    async def bridge(*_args: object) -> str:
        nonlocal unavailable
        unavailable = True
        return "closed"

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
        with patch.object(Session, "get", new=get):
            asyncio.run(terminal.terminal_socket(socket))
    assert not shell.active and not client.app.state.terminal_connections.terminate(terminal_id)
    assert socket.messages[-1] == {
        "type": "error", "message": "SSH 已断开，但会话状态保存失败，请刷新确认",
    }
    assert not any(message["type"] == "closed" for message in socket.messages)
    assert "private-driver-detail" not in caplog.text + str(socket.messages)
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, terminal_id)
        assert item.status == "connected" and item.ended_at is None


@pytest.mark.parametrize("status", ["revoked", "failed"])
def test_cancellation_preserves_previously_sealed_terminal_evidence(
    client: TestClient, terminal_id: str, status: str,
) -> None:
    shell, socket, original = FakeShell(), LifecycleSocket(client), []

    async def bridge(*_args: object) -> str:
        with client.app.state.session_factory() as session:
            item = session.get(TerminalSession, terminal_id)
            terminal_sessions.finish_session(session, item, status, 17, 29, "原始终结原因")
            original.append(item.ended_at)
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
        return "closed"

    async def scenario() -> None:
        task = asyncio.create_task(terminal.terminal_socket(socket))
        with pytest.raises(asyncio.CancelledError):
            await task

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
        asyncio.run(scenario())
    item = assert_finished(client, terminal_id, status)
    assert (item.bytes_in, item.bytes_out, item.failure_reason) == (17, 29, "原始终结原因")
    assert item.ended_at == original[0] and not shell.active
    final = socket.messages[-1]
    assert final["reason"] == status and final["ended_at"] == item.ended_at.isoformat()
    assert final["message"] == "原始终结原因"


@pytest.mark.parametrize("failure", [OSError, anyio.BrokenResourceError, anyio.ClosedResourceError])
def test_transport_close_failure_cannot_escape_terminal_cleanup(
    client: TestClient, terminal_id: str, failure: type[Exception],
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)

    async def bridge(*_args: object) -> str:
        return "terminated"

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
        with patch.object(socket, "close", side_effect=failure) as close:
            asyncio.run(terminal.terminal_socket(socket))
    assert_finished(client, terminal_id, "terminated")
    close.assert_awaited_once_with(code=1000)
    assert not shell.active
