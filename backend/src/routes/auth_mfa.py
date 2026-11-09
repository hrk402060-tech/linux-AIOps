from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_system_admin
from src.database import get_session
from src.schemas.mfa import MfaCode, MfaConfirmResponse, MfaSetupResponse, MfaStatusResponse
from src.services import auth as service
from src.services import auth_policy
from src.services import user_management as management
from src.services.terminal_access import stop_user_terminals

router = APIRouter(tags=["auth"])


@router.get("/users/{user_id}/mfa", response_model=MfaStatusResponse)
def read_mfa_status(
    user_id: str, _principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> MfaStatusResponse:
    try:
        enabled = service.find_user_mfa_status(session, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MfaStatusResponse(enabled=enabled)


@router.post("/users/{user_id}/mfa/setup", response_model=MfaSetupResponse)
def setup_mfa(
    user_id: str, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> MfaSetupResponse:
    user = management.find_user(session, user_id, locked=True)
    before = {"mfa_enabled": user.mfa_enabled}
    try:
        result = service.setup_mfa(session, user, request.app.state.credential_vault)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    management.record_change(
        session, user, principal, "mfa_setup_started", before,
        {"mfa_enabled": False}, "管理员生成 MFA 绑定信息",
    )
    management.commit(session)
    return MfaSetupResponse.model_validate(result)


@router.post("/users/{user_id}/mfa/confirm", response_model=MfaConfirmResponse)
def confirm_mfa(
    user_id: str, payload: MfaCode, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> MfaConfirmResponse:
    user = management.find_user(session, user_id, locked=True)
    try:
        recovery_codes = service.confirm_mfa(
            session, user, payload.code, request.app.state.credential_vault,
        )
    except service.MfaInvalidError as exc:
        raise HTTPException(status_code=400, detail="验证码错误或已过期") from exc
    management.record_change(
        session, user, principal, "mfa_enabled", {"mfa_enabled": False},
        {"mfa_enabled": True}, "管理员确认启用 MFA",
    )
    management.commit(session)
    return MfaConfirmResponse(enabled=True, recovery_codes=recovery_codes)


@router.post("/users/{user_id}/mfa/reset", response_model=MfaStatusResponse)
def reset_mfa(
    user_id: str, request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: Session = Depends(get_session),
) -> MfaStatusResponse:
    try:
        auth_policy.ensure_mfa_reset_allowed(
            session, request.app.state.runtime_settings, user_id,
        )
    except auth_policy.AuthPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    user = management.find_user(session, user_id, locked=True)
    before = {"mfa_enabled": user.mfa_enabled}
    service.reset_mfa(session, user)
    management.record_change(
        session, user, principal, "mfa_reset", before,
        {"mfa_enabled": False}, "管理员重置用户 MFA",
    )
    management.commit(session)
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return MfaStatusResponse(enabled=False)
