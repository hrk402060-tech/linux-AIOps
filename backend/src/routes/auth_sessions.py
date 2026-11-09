from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.schemas.auth import AuthSessionPageResponse, AuthSessionResponse
from src.services import auth as service
from src.services import auth_sessions
from src.services import user_management as management
from src.services.terminal_access import stop_user_terminals

router = APIRouter(tags=["auth"])


@router.get("/sessions", response_model=list[AuthSessionResponse])
def list_sessions(
    request: Request,
    user_id: str | None = Query(default=None, min_length=1, max_length=36),
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> list[AuthSessionResponse]:
    return [
        _session_response(entry)
        for entry in service.list_active_sessions(
            session, _current_hash(request, principal), user_id,
        )
    ]


@router.get("/sessions/page", response_model=AuthSessionPageResponse)
def list_sessions_page(
    request: Request,
    user_id: str | None = Query(default=None, min_length=1, max_length=36),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> AuthSessionPageResponse:
    try:
        entries, total = auth_sessions.page_active_sessions(
            session, _current_hash(request, principal), user_id, offset=offset, limit=limit,
        )
    except auth_sessions.ActiveSessionsUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return AuthSessionPageResponse(
        items=[_session_response(entry) for entry in entries],
        total=total, offset=offset, limit=limit,
    )


def _current_hash(request: Request, principal: AuthPrincipal) -> str | None:
    settings = request.app.state.runtime_settings
    raw_token = request.cookies.get(settings.auth_cookie_name, "")
    return (
        service.hash_token(raw_token)
        if raw_token and principal.auth_type == "session"
        else None
    )


def _session_response(entry: auth_sessions.SessionEntry) -> AuthSessionResponse:
    item, user, is_current = entry
    return AuthSessionResponse(
        id=item.id, user_id=user.id, username=user.username,
        display_name=user.display_name, role=user.role,
        created_at=item.created_at, last_used_at=item.last_used_at,
        expires_at=item.expires_at, client_ip=item.client_ip, user_agent=item.user_agent,
        status="active", is_current=is_current,
    )


@router.post(
    "/users/{user_id}/sessions/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_one_session(
    user_id: str, session_id: str,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> Response:
    result = service.revoke_session_by_id(session, user_id, session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="活动会话不存在或已失效")
    item, user = result
    management.record_change(
        session, user, principal, "session_revoked",
        {
            "session_id": item.id,
            "created_at": item.created_at.isoformat(),
            "last_used_at": item.last_used_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "client_ip": item.client_ip,
            "user_agent": item.user_agent,
        },
        None,
        "管理员撤销单个活动会话",
    )
    management.commit(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_sessions(
    user_id: str, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> Response:
    user = management.find_user(session, user_id, locked=True)
    service.revoke_user_sessions(session, user.id, commit=False)
    management.record_change(session, user, principal, "sessions_revoked", None, None,
                             "管理员撤销用户全部登录与 SSH 会话")
    management.commit(session)
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
