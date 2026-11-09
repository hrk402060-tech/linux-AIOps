from dataclasses import dataclass
from hmac import compare_digest

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.services.auth import get_session_user

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthPrincipal:
    username: str
    role: str
    user_id: str | None
    auth_type: str
    must_change_password: bool = False


def require_authenticated(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthPrincipal:
    expected_token: str = request.app.state.api_admin_token
    provided_token = credentials.credentials if credentials is not None else ""
    valid_scheme = credentials is not None and credentials.scheme.lower() == "bearer"
    if valid_scheme and expected_token and compare_digest(provided_token, expected_token):
        principal = AuthPrincipal("break-glass-admin", "admin", None, "break_glass")
        request.state.auth_principal = principal
        return principal

    settings = request.app.state.runtime_settings
    raw_token = request.cookies.get(settings.auth_cookie_name)
    if raw_token:
        with request.app.state.session_factory() as session:
            result = get_session_user(session, raw_token)
        if result is not None:
            _, user = result
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                validate_request_origin(request)
            principal = AuthPrincipal(
                user.username, user.role, user.id, "session",
                user.must_change_password,
            )
            request.state.auth_principal = principal
            return principal
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthPrincipal:
    principal = require_authenticated(request, credentials)
    if principal.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="必须先修改临时密码",
        )
    if principal.role not in {"admin", "operator", "auditor"}:
        raise HTTPException(status_code=403, detail="当前账号角色无效")
    if principal.role == "auditor" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="审计员账号仅允许只读访问",
        )
    return principal


def require_system_admin(
    principal: AuthPrincipal = Depends(require_admin),
) -> AuthPrincipal:
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可执行此操作",
        )
    return principal


def authenticated_operator(request: Request) -> str:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal.username


def validate_request_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request rejected")
    source = request.headers.get("origin")
    if source is None:
        referer = request.headers.get("referer")
        source = referer.rstrip("/").split("/", maxsplit=3)[:3] if referer else None
        source = "/".join(source) if isinstance(source, list) else source
    if source is not None and not request.app.state.runtime_settings.is_origin_allowed(source):
        raise HTTPException(status_code=403, detail="Request origin is not allowed")
