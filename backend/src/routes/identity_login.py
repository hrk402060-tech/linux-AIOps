from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from src.auth import validate_request_origin
from src.database import get_session
from src.schemas.auth import UserResponse
from src.schemas.identity import IdentityLogin
from src.routes.identity_validation import SafeIdentityRoute
from src.services import identity_login, identity_sessions
from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError

router = APIRouter(
    prefix="/auth/identity", tags=["identity-login"], route_class=SafeIdentityRoute,
)
MFA_COOKIE = "identity_mfa_flow"
OIDC_COOKIE = "identity_oidc_flow"


def client_ip(request: Request) -> str | None:
    return request.client.host[:64] if request.client else None


def flow_cookie(request: Request, response: Response, name: str, token: str) -> None:
    response.set_cookie(
        name, token, max_age=300, httponly=True,
        secure=request.app.state.runtime_settings.auth_cookie_secure,
        samesite="lax", path="/api/v1/auth/identity",
    )


def login_cookie(
    request: Request, response: Response, result: identity_sessions.LoginResult,
) -> None:
    settings = request.app.state.runtime_settings
    response.set_cookie(
        settings.auth_cookie_name, result.token, max_age=result.ttl_seconds,
        httponly=True, secure=settings.auth_cookie_secure, samesite="lax", path="/",
    )
    response.delete_cookie(MFA_COOKIE, path="/api/v1/auth/identity")


@router.get("/options")
def login_options(session: Session = Depends(get_session)) -> dict[str, bool]:
    try:
        return identity_login.options(session)
    except IdentitySourceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/ldap/login")
def login_ldap(
    payload: IdentityLogin, request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    validate_request_origin(request)
    try:
        result = identity_login.ldap_login(
            session, request.app.state.runtime_settings, request.app.state.credential_vault,
            payload.username, payload.password.get_secret_value(), payload.otp_code,
            client_ip(request), request.headers.get("user-agent"),
        )
    except IdentityAuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except IdentitySourceError as error:
        code = 403 if "关联" in str(error) or "邀请" in str(error) else 409
        raise HTTPException(status_code=code, detail=str(error)) from error
    if result.challenge:
        response = JSONResponse({"detail": result.requirement}, status_code=401)
        flow_cookie(request, response, MFA_COOKIE, result.challenge)
        return response
    response = JSONResponse(UserResponse.model_validate(result.user).model_dump(mode="json"))
    login_cookie(request, response, result)
    return response


@router.get("/oidc/start")
def start_oidc(request: Request, session: Session = Depends(get_session)) -> RedirectResponse:
    try:
        url, browser = identity_login.oidc_start(session, request.app.state.credential_vault)
    except IdentitySourceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response = RedirectResponse(url, status_code=302)
    flow_cookie(request, response, OIDC_COOKIE, browser)
    return response


@router.get("/oidc/callback")
def callback_oidc(
    request: Request, state: str = "", code: str = "", error: str | None = None,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        result = identity_login.oidc_callback(
            session, request.app.state.runtime_settings, request.app.state.credential_vault,
            state, request.cookies.get(OIDC_COOKIE, ""), code, error,
            client_ip(request), request.headers.get("user-agent"),
        )
    except (IdentityAuthenticationError, IdentitySourceError) as failure:
        raise HTTPException(status_code=400, detail=str(failure)) from failure
    response = RedirectResponse(
        f"/?identity_mfa={'setup' if result.requirement == 'mfa_setup_required' else 'required'}"
        if result.challenge else "/", status_code=302,
    )
    response.delete_cookie(OIDC_COOKIE, path="/api/v1/auth/identity")
    if result.challenge:
        flow_cookie(request, response, MFA_COOKIE, result.challenge)
    else:
        login_cookie(request, response, result)
    return response
