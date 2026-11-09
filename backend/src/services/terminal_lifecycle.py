from collections.abc import Callable
import asyncio
from dataclasses import dataclass
import logging
from threading import Lock
from typing import Protocol

import anyio
from fastapi import WebSocket
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from src.models.terminal import TerminalSession
from src.services import terminal_sessions
from src.services.terminal_gateway import InteractiveShell

logger = logging.getLogger(__name__)
END_REASONS = {
    "closed": "SSH 会话已结束",
    "terminated": "已按管理员请求断开 SSH 会话",
    "timeout": "SSH 会话空闲或使用时长达到限制",
    "revoked": "登录会话或资产权限已失效",
    "failed": "SSH 会话异常中断",
}


class Traffic(Protocol):
    bytes_in: int
    bytes_out: int


@dataclass
class TerminalOutcome:
    status: str | None = None
    failure_reason: str | None = None

    def decide(self, status: str, reason: str | None = None) -> None:
        if self.status is None:
            self.status = status
            self.failure_reason = reason or END_REASONS[status]


def close_shell(shell: InteractiveShell) -> None:
    try:
        shell.close()
    except Exception as error:
        logger.error("SSH shell cleanup failed: %s", type(error).__name__)


async def close_owned_shell(shell: InteractiveShell) -> None:
    # Dispatch before the first checkpoint so direct task cancellation cannot skip cleanup.
    closing = asyncio.get_running_loop().run_in_executor(None, close_shell, shell)
    with anyio.CancelScope(shield=True):
        await asyncio.shield(closing)


class ShellLease:
    def __init__(self) -> None:
        self._lock = Lock()
        self._shell: InteractiveShell | None = None
        self._abandoned = False

    def open(self, connect: Callable[[], InteractiveShell]) -> None:
        shell = connect()
        with self._lock:
            if not self._abandoned:
                self._shell = shell
                return
        close_shell(shell)

    def claim(self) -> InteractiveShell:
        with self._lock:
            if self._abandoned or self._shell is None:
                raise RuntimeError("SSH connection ownership is unavailable")
            shell, self._shell = self._shell, None
            return shell

    def abandon(self) -> InteractiveShell | None:
        with self._lock:
            self._abandoned = True
            shell, self._shell = self._shell, None
            return shell


async def open_owned_shell(
    connect: Callable[[], InteractiveShell], authorize: Callable[[], None],
) -> InteractiveShell:
    lease = ShellLease()
    try:
        await asyncio.to_thread(lease.open, connect)
        authorize()
        return lease.claim()
    finally:
        orphan = lease.abandon()
        if orphan is not None:
            await close_owned_shell(orphan)


def _persist_terminal(
    factory: sessionmaker[Session], session_id: str,
    outcome: TerminalOutcome, traffic: Traffic,
) -> dict[str, str] | None:
    try:
        with factory() as session:
            item = session.get(TerminalSession, session_id)
            if item is None:
                return None
            terminal_sessions.finish_session(
                session, item, outcome.status or "failed",
                traffic.bytes_in, traffic.bytes_out, outcome.failure_reason,
            )
            if item.ended_at is None:
                return None
            return {
                "type": "closed", "reason": item.status,
                "message": item.failure_reason or END_REASONS.get(item.status, "SSH 会话已结束"),
                "ended_at": item.ended_at.isoformat(),
            }
    except (SQLAlchemyError, terminal_sessions.TerminalSessionError) as error:
        logger.error("SSH terminal finalization failed: %s", type(error).__name__)
        return None


async def send_final_message(websocket: WebSocket, message: dict[str, str]) -> None:
    try:
        await websocket.send_json(message)
    except (
        RuntimeError, WebSocketDisconnect, OSError,
        anyio.BrokenResourceError, anyio.ClosedResourceError,
    ):
        return


async def finalize_terminal(
    websocket: WebSocket, session: Session, session_id: str,
    shell: InteractiveShell | None, outcome: TerminalOutcome, traffic: Traffic,
) -> None:
    try:
        session.close()
    except SQLAlchemyError as error:
        logger.error("SSH database session cleanup failed: %s", type(error).__name__)
    outcome.decide("failed", "SSH 服务任务取消导致会话中断")
    # Persist before awaiting: raw task cancellation cannot interrupt this step.
    result = _persist_terminal(websocket.app.state.session_factory, session_id, outcome, traffic)
    with anyio.CancelScope(shield=True):
        if shell is not None:
            await close_owned_shell(shell)
        if result is None:
            await send_final_message(websocket, {
                "type": "error", "message": "SSH 已断开，但会话状态保存失败，请刷新确认",
            })
        elif result["reason"] == "failed":
            await send_final_message(websocket, {
                "type": "error", "message": result["message"],
                "reason": result["reason"], "ended_at": result["ended_at"],
            })
        else:
            if result["reason"] == "revoked":
                await send_final_message(websocket, {
                    "type": "error", "code": "asset_access_revoked",
                    "message": result["message"], "reason": result["reason"],
                    "ended_at": result["ended_at"],
                })
            await send_final_message(websocket, result)
