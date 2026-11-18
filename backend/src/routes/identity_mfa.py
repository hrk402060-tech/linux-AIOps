from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.auth import validate_request_origin
from src.database import get_session
from src.routes.identity_login import MFA_COOKIE, client_ip, login_cookie
from src.routes.identity_validation import SafeIdentityRoute
from src.schemas.auth import UserResponse
from src.schemas.mfa import MfaCode, MfaSetupResponse
from src.services import auth, identity_sessions, identity_store as store
from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError

router = APIRouter(
    prefix="/auth/identity/mfa", tags=["identity-login"], route_class=SafeIdentityRoute,
)


def _token(request: Request) -> str:
    return request.cookies.get(MFA_COOKIE, "")


@router.get("/status")
def status(request: Request, session: Session = Depends(get_session)) -> dict[str, bool]:
    try:
        user, _ = identity_sessions.challenge_user(
            session, request.app.state.credential_vault, _token(request),
        )
    except (IdentitySourceError, IdentityAuthenticationError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return {"required": True, "enrollment_required": not user.mfa_enabled}


@router.post("/setup", response_model=MfaSetupResponse)
def setup(request: Request, session: Session = Depends(get_session)) -> MfaSetupResponse:
    validate_request_origin(request)
    try:
        store.lock(session)
        user, _ = identity_sessions.challenge_user(
            session, request.app.state.credential_vault, _token(request),
        )
        result = auth.setup_mfa(session, user, request.app.state.credential_vault)
        store.audit(session, "mfa", "mfa_setup_started", user.username, user.id, None, {
            "mfa_enabled": False,
        })
        store.commit(session)
        return MfaSetupResponse.model_validate(result)
    except (IdentitySourceError, IdentityAuthenticationError, ValueError) as error:
        raise HTTPException(status_code=401, detail="MFA 绑定流程不可用，请重新登录") from error


@router.post("/verify")
def verify(
    payload: MfaCode, request: Request, session: Session = Depends(get_session),
) -> JSONResponse:
    validate_request_origin(request)
    try:
        result = identity_sessions.verify_challenge(
            session, request.app.state.runtime_settings, request.app.state.credential_vault,
            _token(request), payload.code, client_ip(request), request.headers.get("user-agent"),
        )
    except (IdentitySourceError, IdentityAuthenticationError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    response = JSONResponse(UserResponse.model_validate(result.user).model_dump(mode="json"))
    login_cookie(request, response, result)
    return response


@router.post("/confirm")
def confirm(
    payload: MfaCode, request: Request, session: Session = Depends(get_session),
) -> JSONResponse:
    validate_request_origin(request)
    try:
        result, recovery_codes = identity_sessions.enroll(
            session, request.app.state.runtime_settings, request.app.state.credential_vault,
            _token(request), payload.code, client_ip(request), request.headers.get("user-agent"),
        )
    except (IdentitySourceError, IdentityAuthenticationError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    response = JSONResponse({
        "user": UserResponse.model_validate(result.user).model_dump(mode="json"),
        "recovery_codes": recovery_codes,
    })
    login_cookie(request, response, result)
    return response
