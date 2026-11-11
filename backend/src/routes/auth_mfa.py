from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, require_authenticated, require_system_admin
from src.database import get_session
from src.models.auth import User
from src.schemas.mfa import (
    MfaCode, MfaConfirmResponse, MfaSelfStatusResponse, MfaSetupResponse, MfaStatusResponse,
)
from src.services import auth as service
from src.services import auth_policy
from src.services import user_management as management
from src.services.terminal_access import stop_user_terminals
from src.routes.auth_mfa_enrollment import router as enrollment_router

router = APIRouter(tags=["auth"])
router.include_router(enrollment_router)


def _current_user(
    principal: AuthPrincipal, session: Session,
) -> User:
    if principal.user_id is None:
        raise HTTPException(status_code=400, detail="应急令牌不能配置个人 MFA")
    return management.find_user(session, principal.user_id, locked=True)


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


@router.get("/me/mfa", response_model=MfaSelfStatusResponse)
def read_own_mfa(
    request: Request,
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> MfaSelfStatusResponse:
    user = _current_user(principal, session)
    policy = auth_policy.resolve(session, request.app.state.runtime_settings)
    return MfaSelfStatusResponse(
        enabled=user.mfa_enabled,
        enforcement=policy.mfa_enforcement,
        required=auth_policy.is_mfa_required(policy, user.role),
        can_disable=auth_policy.can_user_reset_mfa(policy, user.role),
    )


@router.post("/me/mfa/setup", response_model=MfaSetupResponse)
def setup_own_mfa(
    request: Request,
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> MfaSetupResponse:
    user = _current_user(principal, session)
    before = {"mfa_enabled": user.mfa_enabled}
    try:
        result = service.setup_mfa(session, user, request.app.state.credential_vault)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    management.record_change(
        session, user, principal, "mfa_setup_started", before,
        {"mfa_enabled": False}, "用户开始绑定个人 MFA",
    )
    management.commit(session)
    return MfaSetupResponse.model_validate(result)


@router.post("/me/mfa/confirm", response_model=MfaConfirmResponse)
def confirm_own_mfa(
    payload: MfaCode,
    request: Request,
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> MfaConfirmResponse:
    user = _current_user(principal, session)
    try:
        recovery_codes = service.confirm_mfa(
            session, user, payload.code, request.app.state.credential_vault,
        )
    except service.MfaInvalidError as exc:
        raise HTTPException(status_code=400, detail="验证码错误或已过期") from exc
    management.record_change(
        session, user, principal, "mfa_enabled", {"mfa_enabled": False},
        {"mfa_enabled": True}, "用户启用个人 MFA",
    )
    management.commit(session)
    return MfaConfirmResponse(enabled=True, recovery_codes=recovery_codes)


@router.post("/me/mfa/reset", response_model=MfaStatusResponse)
def reset_own_mfa(
    request: Request,
    principal: AuthPrincipal = Depends(require_authenticated),
    session: Session = Depends(get_session),
) -> MfaStatusResponse:
    try:
        policy = auth_policy.lock_policy(session, request.app.state.runtime_settings)
    except auth_policy.AuthPolicyUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    user = _current_user(principal, session)
    if not auth_policy.can_user_reset_mfa(policy, user.role):
        raise HTTPException(status_code=409, detail="当前 MFA 策略要求保持绑定，请联系管理员重置")
    before = {"mfa_enabled": user.mfa_enabled}
    service.reset_mfa(session, user)
    management.record_change(
        session, user, principal, "mfa_reset", before,
        {"mfa_enabled": False}, "用户关闭个人 MFA",
    )
    management.commit(session)
    stop_user_terminals(session, request.app.state.terminal_connections, user.username)
    return MfaStatusResponse(enabled=False)
