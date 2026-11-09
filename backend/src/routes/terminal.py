import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import json
from threading import Event
from time import monotonic
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
import paramiko
from paramiko.ssh_exception import NoValidConnectionsError
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from src.auth import AuthPrincipal, authenticated_operator, require_admin
from src.database import get_session
from src.models.asset import Asset, AssetLifecycleStatus
from src.models.auth import AuthSession, User
from src.models.terminal import TerminalCommandAudit, TerminalSession
from src.schemas.terminal import (
    TerminalSessionResponse, TerminalTicketCreate, TerminalTicketResponse,
)
from src.schemas.terminal_assistant import (
    TerminalAssistantRequest, TerminalAssistantResponse, TerminalCommandResponse,
    TerminalAssistantChatRequest, TerminalAssistantChatResponse, TerminalAssistantChatMessage,
    TerminalCommandReview,
)
from src.services import terminal_assistant, terminal_gateway, terminal_sessions, terminal_lifecycle
from src.services import asset_access
from src.services.auth import get_session_user
from src.services.ai_settings import AISettingsError, terminal_runtime_config
from src.services.terminal_tickets import TerminalTicket

router = APIRouter(prefix="/terminal", tags=["terminal"])


@dataclass
class TerminalTraffic:
    bytes_in: int = 0
    bytes_out: int = 0


@dataclass(frozen=True)
class TerminalAuthContext:
    principal: AuthPrincipal
    auth_session_id: str | None = None


@router.post(
    "/tickets",
    response_model=TerminalTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_ticket(
    payload: TerminalTicketCreate, request: Request,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> TerminalTicketResponse:
    asset = session.get(Asset, payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.lifecycle_status != AssetLifecycleStatus.ENABLED:
        raise HTTPException(status_code=409, detail="已停用或归档资产不能创建终端会话")
    asset_access.require_asset(session, principal, asset.id, ssh=True)
    if asset.credential_id is None:
        raise HTTPException(status_code=400, detail="资产未绑定 SSH 凭据")
    session_id = str(uuid4())
    ticket, expires_at = request.app.state.terminal_tickets.issue(session_id, asset.id)
    try:
        item = terminal_sessions.create_session(
            session, asset, authenticated_operator(request),
            _client_ip(request), expires_at, session_id
        )
    except terminal_sessions.TerminalSessionError as exc:
        request.app.state.terminal_tickets.discard(ticket)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TerminalTicketResponse(
        ticket=ticket, session_id=item.id,
        websocket_path="/api/v1/terminal/ws", expires_at=expires_at,
    )


@router.get("/sessions", response_model=list[TerminalSessionResponse])
def read_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> list[TerminalSessionResponse]:
    return [
        TerminalSessionResponse.model_validate(item)
        for item in terminal_sessions.list_sessions(
            session, limit, None if principal.role == "admin" else principal.username
        )
    ]


@router.post(
    "/assistant/analyze", response_model=TerminalAssistantResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_terminal(
    payload: TerminalAssistantRequest, request: Request,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> TerminalAssistantResponse:
    _require_owned_terminal(session, payload.session_id, principal)
    try:
        analysis, commands = terminal_assistant.analyze(
            session,
            payload.model_copy(update={
                "operator": authenticated_operator(request), "mode": "analysis_only",
            }),
        )
    except terminal_assistant.TerminalAssistantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TerminalAssistantResponse(
        id=analysis.id, terminal_session_id=analysis.terminal_session_id,
        mode=analysis.mode, redacted_context=analysis.redacted_context,
        diagnosis=analysis.diagnosis, plan=analysis.plan,
        validation_steps=analysis.validation_steps, operator=analysis.operator,
        created_at=analysis.created_at,
        commands=[TerminalCommandResponse.model_validate(item) for item in commands],
    )


@router.get("/assistant/commands", response_model=list[TerminalCommandResponse])
def read_assistant_commands(
    session_id: str = Query(min_length=36, max_length=36),
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> list[TerminalCommandResponse]:
    _require_owned_terminal(session, session_id, principal)
    return [
        TerminalCommandResponse.model_validate(item)
        for item in terminal_assistant.list_commands(session, session_id)
    ]


@router.patch(
    "/assistant/commands/{command_id}/review",
    response_model=TerminalCommandResponse,
)
def review_assistant_command(
    command_id: str, payload: TerminalCommandReview,
    request: Request, session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> TerminalCommandResponse:
    if payload.decision == "approved":
        raise HTTPException(status_code=403, detail="AI 仅允许只读分析，不允许批准或下发命令")
    item = session.get(TerminalCommandAudit, command_id)
    if item is None:
        raise HTTPException(status_code=404, detail="建议命令不存在")
    _require_owned_terminal(session, item.terminal_session_id, principal)
    try:
        reviewed = terminal_assistant.review_command(
            session, item, payload.decision, authenticated_operator(request),
            payload.confirmation
        )
    except terminal_assistant.TerminalAssistantReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except terminal_assistant.TerminalAssistantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TerminalCommandResponse.model_validate(reviewed)


@router.post("/sessions/{session_id}/disconnect", status_code=status.HTTP_202_ACCEPTED)
def disconnect_session(
    session_id: str, request: Request, principal: AuthPrincipal = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    item = session.get(TerminalSession, session_id)
    if item is None:
        raise HTTPException(status_code=404, detail="终端会话不存在")
    if principal.role != "admin" and item.operator != principal.username:
        raise HTTPException(status_code=403, detail="当前账号不能断开其他用户的终端会话")
    if not request.app.state.terminal_connections.terminate(session_id):
        raise HTTPException(status_code=409, detail="Terminal session is not connected")
    return {"status": "terminating"}


@router.websocket("/ws")
async def terminal_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    origin = websocket.headers.get("origin")
    if origin is not None and not websocket.app.state.runtime_settings.is_origin_allowed(origin):
        await websocket.close(code=4403)
        return
    auth = await _authenticate(websocket)
    if auth is None:
        return
    ticket, columns, rows, auth_context = auth
    stop = websocket.app.state.terminal_connections.acquire(ticket.session_id)
    if stop is None:
        _fail_waiting_session(websocket, ticket.session_id, "终端并发连接数已达上限")
        await websocket.close(code=4429)
        return
    try:
        await _run_terminal(
            websocket, ticket.session_id, columns, rows, stop, auth_context
        )
    finally:
        websocket.app.state.terminal_connections.release(ticket.session_id)
        await _safe_close(websocket)


async def _authenticate(
    websocket: WebSocket,
) -> tuple[TerminalTicket, int, int, TerminalAuthContext] | None:
    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if not isinstance(data, dict):
            raise ValueError("Invalid authentication message")
        ticket = websocket.app.state.terminal_tickets.consume(str(data.get("ticket", "")))
        columns, rows = int(data.get("columns", 80)), int(data.get("rows", 24))
        valid = data.get("type") == "auth" and 20 <= columns <= 400 and 5 <= rows <= 200
    except (TimeoutError, ValueError, WebSocketDisconnect, json.JSONDecodeError):
        ticket, valid, columns, rows = None, False, 80, 24
    if ticket is None or not valid:
        await websocket.close(code=4401)
        return None
    settings = websocket.app.state.runtime_settings
    authorization = websocket.headers.get("authorization", "")
    expected = websocket.app.state.api_admin_token
    if authorization.lower().startswith("bearer ") and (
        expected and authorization[7:] == expected
    ):
        return ticket, columns, rows, TerminalAuthContext(
            AuthPrincipal("break-glass-admin", "admin", None, "break_glass")
        )
    raw_token = websocket.cookies.get(settings.auth_cookie_name)
    if raw_token:
        with websocket.app.state.session_factory() as session:
            result = get_session_user(session, raw_token)
        if result is not None:
            auth_session, user = result
            return ticket, columns, rows, TerminalAuthContext(
                AuthPrincipal(
                    user.username, user.role, user.id, "session",
                    user.must_change_password,
                ),
                auth_session.id,
            )
    await websocket.close(code=4401)
    return None


async def _run_terminal(
    websocket: WebSocket, session_id: str, columns: int, rows: int, stop: Event,
    auth_context: TerminalAuthContext,
) -> None:
    session = websocket.app.state.session_factory()
    shell: terminal_gateway.InteractiveShell | None = None
    item: TerminalSession | None = None
    traffic = TerminalTraffic()
    outcome = terminal_lifecycle.TerminalOutcome()
    try:
        item, shell = await _open(
            websocket, session, session_id, columns, rows, auth_context
        )
        await websocket.send_json({"type": "connected", "session_id": session_id})
        final_status = await _bridge(
            websocket, shell, stop, traffic, session, session_id,
            websocket.app.state.session_factory, auth_context, item.asset_id, outcome,
        )
        outcome.decide(final_status)
    except WebSocketDisconnect as error:
        outcome.decide("closed", "浏览器主动断开 SSH 会话" if error.code == 1000 else "浏览器连接中断")
    except asyncio.CancelledError:
        outcome.decide("failed", "SSH 服务任务取消导致会话中断")
        raise
    except Exception as exc:
        outcome.decide("failed", _ssh_failure_message(exc))
    finally:
        await terminal_lifecycle.finalize_terminal(
            websocket, session, session_id, shell, outcome, traffic,
        )


async def _open(
    websocket: WebSocket, session: Session, session_id: str, columns: int, rows: int,
    auth_context: TerminalAuthContext,
) -> tuple[TerminalSession, terminal_gateway.InteractiveShell]:
    username = auth_context.principal.username
    item = session.get(TerminalSession, session_id)
    if item is None or item.status != "waiting" or item.operator != username:
        raise ValueError("Invalid terminal session")
    _require_socket_access(session, auth_context, item.asset_id)
    asset = session.get(Asset, item.asset_id)
    if (
        asset is None
        or asset.lifecycle_status != AssetLifecycleStatus.ENABLED
        or asset.credential is None
    ):
        raise ValueError("Asset credential unavailable")
    credential = asset.credential
    vault = websocket.app.state.credential_vault
    secret = vault.decrypt(
        credential.encrypted_secret, credential.nonce, credential.id,
        credential.credential_type.value, credential.key_version,
    )
    def authorize_connection() -> None:
        with websocket.app.state.session_factory() as auth_session:
            _require_socket_access(auth_session, auth_context, item.asset_id)
        terminal_sessions.mark_connected(session, item)

    shell = await terminal_lifecycle.open_owned_shell(partial(
        terminal_gateway.open_shell, asset, credential, secret, columns, rows,
    ), authorize_connection)
    return item, shell


async def _bridge(
    websocket: WebSocket, shell: terminal_gateway.InteractiveShell,
    stop: Event, traffic: TerminalTraffic, session: Session, session_id: str,
    session_factory: sessionmaker[Session],
    auth_context: TerminalAuthContext, asset_id: str,
    outcome: terminal_lifecycle.TerminalOutcome,
) -> str:
    started = last_activity = monotonic()
    last_authorization_check = 0.0
    while shell.is_active():
        if stop.is_set():
            return "terminated"
        if monotonic() - last_authorization_check >= 1:
            try:
                with session_factory() as auth_session:
                    _require_socket_access(auth_session, auth_context, asset_id)
            except ValueError as exc:
                outcome.decide("revoked", str(exc))
                return "revoked"
            last_authorization_check = monotonic()
        status = _timeout_status(websocket, started, last_activity)
        if status:
            return status
        if shell.has_output():
            output = await asyncio.to_thread(shell.receive)
            if output:
                text = output.decode("utf-8", errors="replace")
                traffic.bytes_out += len(output)
                last_activity = monotonic()
                await websocket.send_json({"type": "output", "data": text})
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=0.05)
        except TimeoutError:
            continue
        if not isinstance(message, dict):
            raise ValueError("Invalid terminal message")
        if message.get("type") in {"input", "resize", "approved_command"}:
            try:
                with session_factory() as auth_session:
                    _require_socket_access(auth_session, auth_context, asset_id)
            except ValueError as exc:
                outcome.decide("revoked", str(exc))
                return "revoked"
        if message.get("type") == "approved_command":
            await websocket.send_json({
                "type": "error", "code": "ai_execution_disabled",
                "message": "AI 仅允许只读分析；请由管理员独立确认并使用人工 SSH 处理",
            })
        else:
            traffic.bytes_in += await _handle_message(shell, message)
        last_activity = monotonic()
    return "closed"


def _require_socket_access(
    session: Session, auth_context: TerminalAuthContext, asset_id: str
) -> None:
    session.expire_all()
    principal = auth_context.principal
    if auth_context.auth_session_id is not None:
        auth_session = session.get(AuthSession, auth_context.auth_session_id)
        expires_at = auth_session.expires_at if auth_session is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError("登录会话已失效")
        user = session.get(User, principal.user_id)
        if user is None:
            raise ValueError("登录账号已失效")
        principal = AuthPrincipal(
            user.username, user.role, user.id, "session", user.must_change_password
        )
    try:
        asset_access.require_asset(session, principal, asset_id, ssh=True)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


async def _handle_message(
    shell: terminal_gateway.InteractiveShell, message: dict[str, object]
) -> int:
    if message.get("type") == "input":
        value = str(message.get("data", ""))
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("Terminal input too large")
        await asyncio.to_thread(shell.send, value)
        return len(value.encode("utf-8"))
    if message.get("type") == "resize":
        columns, rows = int(message.get("columns", 80)), int(message.get("rows", 24))
        if not 20 <= columns <= 400 or not 5 <= rows <= 200:
            raise ValueError("Invalid terminal dimensions")
        await asyncio.to_thread(shell.resize, columns, rows)
    return 0


def _timeout_status(websocket: WebSocket, started: float, activity: float) -> str | None:
    now = monotonic()
    if now - activity >= websocket.app.state.terminal_idle_timeout:
        return "timeout"
    if now - started >= websocket.app.state.terminal_max_duration:
        return "timeout"
    return None


async def _safe_send(websocket: WebSocket, message: dict[str, str]) -> None:
    try:
        await websocket.send_json(message)
    except (RuntimeError, WebSocketDisconnect):
        return

async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close(code=1000)
    except (
        RuntimeError, WebSocketDisconnect, OSError,
        anyio.BrokenResourceError, anyio.ClosedResourceError,
    ):
        return


def _client_ip(request: Request) -> str:
    value = request.headers.get("x-real-ip")
    if value and len(value) <= 64:
        return value
    return request.client.host[:64] if request.client else "unknown"


def _ssh_failure_message(exc: Exception) -> str:
    if isinstance(exc, paramiko.AuthenticationException):
        return "SSH 登录认证失败：请检查资产中的用户名、密码或私钥"
    if isinstance(exc, NoValidConnectionsError):
        return "SSH 端口无法连接：请检查服务器地址、防火墙和端口"
    if isinstance(exc, TimeoutError):
        return "SSH 连接超时：请检查服务器是否在线及端口是否可达"
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"SSH 网络连接失败：{exc}"
    if isinstance(exc, paramiko.SSHException):
        return f"SSH 连接失败：{exc}"
    return f"SSH 终端连接失败：{type(exc).__name__}"


def _fail_waiting_session(websocket: WebSocket, session_id: str, reason: str) -> None:
    with websocket.app.state.session_factory() as session:
        item = session.get(TerminalSession, session_id)
        if item is not None:
            terminal_sessions.finish_session(session, item, "failed", 0, 0, reason)
@router.post("/assistant/chat", response_model=TerminalAssistantChatResponse, status_code=status.HTTP_201_CREATED)
def chat_terminal_assistant(
    payload: TerminalAssistantChatRequest, request: Request,
    session: Session = Depends(get_session),
    principal: AuthPrincipal = Depends(require_admin),
) -> TerminalAssistantChatResponse:
    operator = authenticated_operator(request)
    terminal = session.get(TerminalSession, payload.session_id)
    if terminal is None or terminal.status not in {"waiting", "connected"}:
        raise HTTPException(status_code=409, detail="仅等待连接或已连接会话可以使用终端助手")
    if principal.role != "admin" and terminal.operator != principal.username:
        raise HTTPException(status_code=403, detail="当前账号不能访问其他用户的终端助手会话")
    asset_access.require_asset(session, principal, terminal.asset_id, ssh=True)
    history = [{"role": item.role, "content": item.content} for item in payload.history]
    try:
        configuration = terminal_runtime_config(session, request.app.state.credential_vault)
        reply, engine = terminal_assistant.chat_reply(configuration, payload.message, payload.context, history)
        analysis_payload = TerminalAssistantRequest(session_id=payload.session_id, mode="analysis_only", question=payload.message, context=payload.context, operator=operator)
        analysis, commands = terminal_assistant.analyze(session, analysis_payload)
    except terminal_assistant.TerminalAssistantError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AISettingsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    analysis_response = TerminalAssistantResponse(id=analysis.id, terminal_session_id=analysis.terminal_session_id, mode=analysis.mode, redacted_context=analysis.redacted_context, diagnosis=analysis.diagnosis, plan=analysis.plan, validation_steps=analysis.validation_steps, operator=analysis.operator, created_at=analysis.created_at, commands=[TerminalCommandResponse.model_validate(item) for item in commands])
    next_history = (history + [{"role": "user", "content": payload.message}, {"role": "assistant", "content": reply}])[-20:]
    return TerminalAssistantChatResponse(conversation_id=analysis.id, engine=engine, reply=reply, analysis=analysis_response, history=[TerminalAssistantChatMessage.model_validate(item) for item in next_history])


def _require_owned_terminal(
    session: Session, session_id: str, principal: AuthPrincipal
) -> TerminalSession:
    item = session.get(TerminalSession, session_id)
    if item is None:
        raise HTTPException(status_code=404, detail="终端会话不存在")
    if principal.role != "admin" and item.operator != principal.username:
        raise HTTPException(status_code=403, detail="当前账号不能访问其他用户的终端会话")
    asset_access.require_asset(session, principal, item.asset_id, ssh=True)
    return item
