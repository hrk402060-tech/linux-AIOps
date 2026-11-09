import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import anyio
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from src.auth import AuthPrincipal
from src.models.terminal import TerminalSession
from src.routes import terminal
from src.services import terminal_lifecycle, terminal_sessions
from test_terminal_api import FakeShell
import test_terminal_session_finalization as finalization

terminal_id = finalization.terminal_id


class LifecycleSocket:
    def __init__(self, client: TestClient) -> None:
        self.app = client.app
        self.headers = {"origin": "http://localhost:5173"}
        self.messages: list[dict[str, object]] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def send_json(self, value: dict[str, object]) -> None:
        self.messages.append(value)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def patches(client: TestClient, terminal_id: str, shell: FakeShell) -> tuple:
    context = terminal.TerminalAuthContext(
        AuthPrincipal("break-glass-admin", "admin", None, "break_glass"),
    )

    async def authenticated(_socket: object) -> tuple:
        return SimpleNamespace(session_id=terminal_id), 80, 24, context

    async def opened(_socket: object, session: object, *_args: object) -> tuple:
        item = session.get(TerminalSession, terminal_id)
        terminal_sessions.mark_connected(session, item)
        return item, shell

    return patch.object(terminal, "_authenticate", new=authenticated), patch.object(
        terminal, "_open", new=opened,
    )


def assert_finished(client: TestClient, terminal_id: str, status: str) -> TerminalSession:
    assert not client.app.state.terminal_connections.terminate(terminal_id)
    with client.app.state.session_factory() as session:
        item = session.get(TerminalSession, terminal_id)
        assert item.status == status and item.ended_at is not None
        return item


@pytest.mark.parametrize("mode", ["anyio_scope", "asyncio_task"])
def test_real_cancellation_persists_terminal_before_cleanup_await(
    client: TestClient, terminal_id: str, mode: str,
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)

    async def scenario() -> None:
        started = asyncio.Event()

        async def bridge(*_args: object) -> str:
            started.set()
            await asyncio.Event().wait()
            return "closed"

        auth_patch, open_patch = patches(client, terminal_id, shell)
        with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
            if mode == "anyio_scope":
                async with anyio.create_task_group() as group:
                    group.start_soon(terminal.terminal_socket, socket)
                    await asyncio.wait_for(started.wait(), timeout=3)
                    group.cancel_scope.cancel()
            else:
                task = asyncio.create_task(terminal.terminal_socket(socket))
                await asyncio.wait_for(started.wait(), timeout=3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(scenario())
    item = assert_finished(client, terminal_id, "failed")
    assert "取消" in item.failure_reason and not shell.active and shell.sent == []


def test_close_exception_does_not_skip_persistence_or_release(
    client: TestClient, terminal_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)

    def broken_close() -> None:
        shell.active = False
        raise OSError("private-close-detail")

    async def bridge(*_args: object) -> str:
        return "terminated"

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(shell, "close", side_effect=broken_close):
        with patch.object(terminal, "_bridge", new=bridge):
            asyncio.run(terminal.terminal_socket(socket))
    assert_finished(client, terminal_id, "terminated")
    assert not shell.active and "private-close-detail" not in caplog.text


@pytest.mark.parametrize("stage", ["connected_send", "receive"])
def test_peer_disconnect_seals_record_and_closes_shell(
    client: TestClient, terminal_id: str, stage: str,
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)

    async def disconnected(*_args: object) -> str:
        raise WebSocketDisconnect(code=1006)

    async def send(value: dict[str, object]) -> None:
        if value["type"] == "connected":
            raise WebSocketDisconnect(code=1006)

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=disconnected):
        with patch.object(socket, "send_json", new=send if stage == "connected_send" else socket.send_json):
            asyncio.run(terminal.terminal_socket(socket))
    assert_finished(client, terminal_id, "closed")
    assert not shell.active


def test_cancelled_open_worker_closes_late_shell(
    client: TestClient, terminal_id: str,
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)
    started, release, worker_done = Event(), Event(), Event()
    context = terminal.TerminalAuthContext(
        AuthPrincipal("break-glass-admin", "admin", None, "break_glass"),
    )
    original_close = shell.close

    def closed() -> None:
        original_close()
        worker_done.set()

    def slow_open(*_args: object) -> FakeShell:
        started.set()
        assert release.wait(timeout=5)
        return shell

    async def scenario() -> None:
        task = asyncio.create_task(terminal._run_terminal(
            socket, terminal_id, 80, 24, Event(), context,
        ))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        assert await asyncio.to_thread(worker_done.wait, 3)

    with patch("src.services.terminal_gateway.open_shell", side_effect=slow_open):
        with patch.object(shell, "close", side_effect=closed):
            asyncio.run(scenario())
    item = assert_finished(client, terminal_id, "failed")
    assert item.connected_at is None and not shell.active


def test_cancellation_after_persist_still_dispatches_shell_close(
    client: TestClient, terminal_id: str,
) -> None:
    shell, socket, closed = FakeShell(), LifecycleSocket(client), Event()
    original_persist = terminal_lifecycle._persist_terminal
    original_close = shell.close

    def persist_then_cancel(*args: object) -> dict[str, str] | None:
        result = original_persist(*args)
        asyncio.current_task().cancel()
        return result

    def close() -> None:
        original_close()
        closed.set()

    async def bridge(*_args: object) -> str:
        return "terminated"

    async def scenario() -> None:
        task = asyncio.create_task(terminal.terminal_socket(socket))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 3)

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
        with patch.object(terminal_lifecycle, "_persist_terminal", side_effect=persist_then_cancel):
            with patch.object(shell, "close", side_effect=close):
                asyncio.run(scenario())
    assert_finished(client, terminal_id, "terminated")
    assert not shell.active


def test_authorization_cancellation_still_closes_opened_orphan() -> None:
    shell, closed = FakeShell(), Event()
    original_close = shell.close

    def close() -> None:
        original_close()
        closed.set()

    def authorize() -> None:
        asyncio.current_task().cancel()
        raise ValueError("authorization revoked")

    async def scenario() -> None:
        task = asyncio.create_task(terminal_lifecycle.open_owned_shell(lambda: shell, authorize))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 3)

    with patch.object(shell, "close", side_effect=close):
        asyncio.run(scenario())
    assert not shell.active


@pytest.mark.parametrize("failure", [OSError, anyio.BrokenResourceError, anyio.ClosedResourceError])
def test_final_message_transport_failure_preserves_cleanup(
    client: TestClient, terminal_id: str, failure: type[Exception],
) -> None:
    shell, socket = FakeShell(), LifecycleSocket(client)

    async def send(value: dict[str, object]) -> None:
        if value["type"] == "closed":
            raise failure()

    async def bridge(*_args: object) -> str:
        return "terminated"

    auth_patch, open_patch = patches(client, terminal_id, shell)
    with auth_patch, open_patch, patch.object(terminal, "_bridge", new=bridge):
        with patch.object(socket, "send_json", new=send):
            asyncio.run(terminal.terminal_socket(socket))
    assert_finished(client, terminal_id, "terminated")
    assert socket.closed and not shell.active
