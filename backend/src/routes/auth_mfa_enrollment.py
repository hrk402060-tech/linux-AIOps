import base64
from datetime import datetime, timezone
import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal, validate_request_origin
from src.database import get_session
from src.models.auth import User
from src.schemas.mfa import (
    MfaConfirmResponse, MfaEnrollmentCode, MfaEnrollmentSetupResponse, MfaEnrollmentStart,
)
from src.services import auth as service
from src.services import auth_policy
from src.services import user_management as management

router = APIRouter(tags=["auth"])


def _signing_secret(request: Request) -> bytes:
    value = request.app.state.api_admin_token
    text = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return text.encode("utf-8")


def _enrollment_revision(user: User) -> str:
    # A new setup, password reset or permission change invalidates old challenges.
    value = f"{user.password_hash}:{user.mfa_secret_encrypted}:{user.access_revision}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _enrollment_token(user: User, secret: bytes) -> str:
    payload = f"{user.id}:{int(time.time()) + 600}:{_enrollment_revision(user)}".encode()
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return ".".join(base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
                    for part in (payload, signature))


def _read_token(token: str, secret: bytes) -> tuple[str, str]:
    try:
        encoded, signed = token.split(".", maxsplit=1)
        payload, signature = (
            base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
            for part in (encoded, signed)
        )
        expected = hmac.new(secret, payload, hashlib.sha256).digest()
        user_id, expires, revision = payload.decode("utf-8").split(":")
        if not hmac.compare_digest(signature, expected) or int(expires) <= int(time.time()):
            raise ValueError
        return user_id, revision
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(status_code=401, detail="MFA 绑定流程已过期，请重新登录") from None


@router.post("/mfa/enroll/start", response_model=MfaEnrollmentSetupResponse)
def start_forced_mfa_enrollment(
    payload: MfaEnrollmentStart, request: Request,
    session: Session = Depends(get_session),
) -> MfaEnrollmentSetupResponse:
    validate_request_origin(request)
    try:
        policy = auth_policy.lock_policy(session, request.app.state.runtime_settings)
        user, _ = service.authenticate(
            session, payload.username, payload.password, _client_ip(request),
            request.headers.get("user-agent"), policy.session_ttl_seconds,
            policy.login_failure_threshold, policy.lock_duration_seconds,
            None, request.app.state.credential_vault, False, "off", issue_session=False,
        )
    except service.AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from exc
    except (service.MfaRequiredError, service.MfaInvalidError) as exc:
        raise HTTPException(status_code=409, detail="当前账号已绑定 MFA，请正常登录") from exc
    except auth_policy.AuthPolicyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not auth_policy.is_mfa_required(policy, user.role):
        raise HTTPException(status_code=409, detail="请登录后在个人安全中绑定 MFA")
    result = service.setup_mfa(session, user, request.app.state.credential_vault)
    token = _enrollment_token(user, _signing_secret(request))
    _record_enrollment(session, user, "mfa_setup_started", "强制 MFA 登录流程开始绑定")
    management.commit(session)
    return MfaEnrollmentSetupResponse(**result, enrollment_token=token)


@router.post("/mfa/enroll/confirm", response_model=MfaConfirmResponse)
def confirm_forced_mfa_enrollment(
    payload: MfaEnrollmentCode, request: Request,
    session: Session = Depends(get_session),
) -> MfaConfirmResponse:
    validate_request_origin(request)
    user_id, revision = _read_token(payload.enrollment_token, _signing_secret(request))
    try:
        policy = auth_policy.lock_policy(session, request.app.state.runtime_settings)
    except auth_policy.AuthPolicyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    user = management.find_user(session, user_id, locked=True)
    now = datetime.now(timezone.utc)
    if (not user.enabled or user.mfa_enabled or service._is_locked(user, now)
            or not hmac.compare_digest(revision, _enrollment_revision(user))):
        raise HTTPException(status_code=401, detail="MFA 绑定流程已失效，请重新登录")
    if not auth_policy.is_mfa_required(policy, user.role):
        raise HTTPException(status_code=409, detail="请登录后在个人安全中绑定 MFA")
    try:
        recovery_codes = service.confirm_mfa(
            session, user, payload.code, request.app.state.credential_vault,
        )
    except service.MfaInvalidError as exc:
        service._record_failed_attempt(
            user, now, policy.login_failure_threshold, policy.lock_duration_seconds,
        )
        service._record_login(
            session, user, user.username, False, "invalid_mfa",
            _client_ip(request), request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=400, detail="验证码错误或已过期") from exc
    user.failed_login_attempts = 0
    user.locked_until = None
    _record_enrollment(session, user, "mfa_enabled", "用户完成强制 MFA 绑定")
    management.commit(session)
    return MfaConfirmResponse(enabled=True, recovery_codes=recovery_codes)


def _record_enrollment(session: Session, user: User, action: str, reason: str) -> None:
    actor = AuthPrincipal(user.username, user.role, user.id, "mfa_enrollment")
    management.record_change(
        session, user, actor, action, {"mfa_enabled": False},
        {"mfa_enabled": user.mfa_enabled}, reason,
    )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", maxsplit=1)[0].strip()[:64]
    return request.client.host[:64] if request.client else None
