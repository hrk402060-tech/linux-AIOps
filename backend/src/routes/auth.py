from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import (
    AuthPrincipal, require_authenticated, require_system_admin,
    validate_request_origin,
)
from src.database import get_session
from src.models.auth import User
from src.schemas.auth import (
    LoginRequest, PasswordChange, PasswordReset, UserCreate, UserResponse, UserUpdate,
)
from src.schemas.asset_access import UserAccessResponse, UserAccessUpdate
from src.services import auth as service
from src.services import auth_policy
from src.services import user_management as management
from src.services.terminal_access import stop_user_terminals
from src.routes.auth_mfa import router as mfa_router
from src.routes.auth_sessions import router as sessions_router

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(mfa_router)
router.include_router(sessions_router)


@router.post("/login", response_model=UserResponse)
def login(
    payload: LoginRequest, request: Request, response: Response,
    session: Session = Depends(get_session),
) -> UserResponse:
    validate_request_origin(request)
    settings = request.app.state.runtime_settings
    try:
        policy = auth_policy.resolve(session, settings)
    except auth_policy.AuthPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        user, raw_token = service.authenticate(
            session, payload.username, payload.password, _client_ip(request),
            request.headers.get("user-agent"), policy.session_ttl_seconds,
            policy.login_failure_threshold, policy.lock_duration_seconds,
            payload.otp_code, request.app.state.credential_vault,
            policy.require_mfa,
            policy.mfa_enforcement,
        )
    except service.MfaSetupRequiredError as exc:
        raise HTTPException(status_code=401, detail="mfa_setup_required") from exc
    except service.MfaRequiredError as exc:
        raise HTTPException(status_code=401, detail="mfa_required") from exc
    except service.MfaInvalidError as exc:
        raise HTTPException(status_code=401, detail="mfa_invalid") from exc
    except service.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from exc
    response.set_cookie(
        settings.auth_cookie_name,
        raw_token,
        max_age=policy.session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, session: Session = Depends(get_session),
) -> Response:
    validate_request_origin(request)
    settings = request.app.state.runtime_settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    if raw_token:
        service.revoke_session(session, raw_token)
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
def current_user(
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> UserResponse:
    if principal.user_id is None:
        raise HTTPException(status_code=401, detail="应急令牌不对应普通用户会话")
    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="会话已失效")
    return UserResponse.model_validate(user)


@router.post("/change-password", response_model=UserResponse)
def change_own_password(
    payload: PasswordChange, request: Request,
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> UserResponse:
    if principal.user_id is None:
        raise HTTPException(status_code=400, detail="应急令牌不能修改用户密码")
    user = _find_user(session, principal.user_id)
    raw_token = request.cookies.get(request.app.state.runtime_settings.auth_cookie_name, "")
    try:
        changed = service.change_own_password(
            session, user, payload.current_password, payload.new_password,
            service.hash_token(raw_token),
        )
    except service.AuthenticationError as exc:
        raise HTTPException(status_code=400, detail="当前密码错误") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UserResponse.model_validate(changed)


@router.get("/users", response_model=list[UserResponse])
def list_users(
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> list[UserResponse]:
    users = session.scalars(select(User).order_by(User.created_at.asc())).all()
    return [UserResponse.model_validate(user) for user in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def add_user(
    payload: UserCreate, principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserResponse:
    try:
        user = service.create_user(
            session, payload.username, payload.password, payload.role,
            payload.display_name, payload.must_change_password,
            payload.asset_scope, commit=False,
        )
        management.record_change(session, user, principal, "user_created", None,
                                 management.snapshot(user), "管理员创建平台账号")
        management.commit(session)
    except service.UserConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def change_user(
    user_id: str, payload: UserUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserResponse:
    changes = payload.model_dump(exclude_unset=True)
    removing = changes.get("enabled") is False or (
        "role" in changes and changes["role"] != "admin"
    )
    if removing:
        try:
            auth_policy.ensure_bound_mfa_admin_remains(
                session, request.app.state.runtime_settings, user_id,
                action="停用或降级",
            )
        except auth_policy.AuthPolicyUnavailableError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    user = management.update_user(
        session, user_id, changes, principal,
    )
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/reset-password", response_model=UserResponse)
def change_password(
    user_id: str, payload: PasswordReset, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = management.find_user(session, user_id, locked=True)
    before = management.snapshot(user)
    service.reset_password(session, user, payload.password, payload.must_change_password, commit=False)
    management.record_change(session, user, principal, "password_reset", before,
                             management.snapshot(user), "管理员重置密码并撤销会话")
    management.commit(session)
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return UserResponse.model_validate(user)


@router.post("/users/{user_id}/unlock", response_model=UserResponse)
def unlock_user(
    user_id: str,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserResponse:
    user = management.find_user(session, user_id, locked=True)
    before = management.snapshot(user)
    service.unlock_user(session, user, commit=False)
    management.record_change(
        session, user, principal, "user_unlocked", before,
        management.snapshot(user), "管理员解除登录锁定",
    )
    management.commit(session)
    return UserResponse.model_validate(user)


@router.get("/users/{user_id}/asset-access", response_model=UserAccessResponse)
def read_asset_access(
    user_id: str, _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserAccessResponse:
    return management.read_access(session, management.find_user(session, user_id))


@router.put("/users/{user_id}/asset-access", response_model=UserAccessResponse)
def update_asset_access(
    user_id: str, payload: UserAccessUpdate, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> UserAccessResponse:
    result = management.replace_access(session, user_id, payload, principal)
    user = management.find_user(session, user_id)
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return result


def _find_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", maxsplit=1)[0].strip()[:64]
    return request.client.host[:64] if request.client else None
