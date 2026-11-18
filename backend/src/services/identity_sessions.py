from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import Settings
from src.models.auth import AuthSession, LoginAudit, User
from src.services import auth, auth_policy, identity_bindings, identity_flows
from src.services import identity_sources, identity_store as store
from src.services.crypto import CredentialVault
from src.services.identity_store import IdentityAuthenticationError, IdentitySourceError


@dataclass(frozen=True)
class LoginResult:
    user: User
    token: str = ""
    challenge: str = ""
    requirement: str = ""
    ttl_seconds: int = 0


def revision(user: User) -> str:
    return sha256(
        f"{user.id}:{user.password_hash}:{user.access_revision}".encode(),
    ).hexdigest()


def _audit(
    session: Session, user: User, success: bool, reason: str,
    client_ip: str | None, user_agent: str | None,
) -> None:
    session.add(LoginAudit(
        user_id=user.id, username=user.username, success=success, reason=reason,
        client_ip=client_ip, user_agent=user_agent[:512] if user_agent else None,
    ))


def _issue(
    session: Session, user: User, provider: str, subject: str, source_revision: str,
    ttl: int, client_ip: str | None, user_agent: str | None,
) -> LoginResult:
    now = store.utcnow()
    token = secrets.token_urlsafe(48)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    session.add(AuthSession(
        token_hash=auth.hash_token(token), user_id=user.id,
        expires_at=now + timedelta(seconds=ttl), client_ip=client_ip,
        user_agent=user_agent[:512] if user_agent else None,
    ))
    identity_bindings.mark_verified(session, provider, subject, source_revision)
    _audit(session, user, True, f"{provider}_success", client_ip, user_agent)
    store.commit(session)
    return LoginResult(user=user, token=token, ttl_seconds=ttl)


def finish(
    session: Session, settings: Settings, vault: CredentialVault,
    provider: str, subject: str, source_revision: str,
    client_ip: str | None, user_agent: str | None, otp_code: str | None = None,
) -> LoginResult:
    store.lock(session)
    identity_sources.ensure_current(session, provider, source_revision)
    user = identity_bindings.resolve(session, provider, subject)
    user = session.scalar(select(User).where(User.id == user.id).with_for_update()
                          .execution_options(populate_existing=True))
    if user is None or not user.enabled or auth._is_locked(user, store.utcnow()):
        raise IdentityAuthenticationError("账号已停用或已锁定")
    policy = auth_policy.resolve(session, settings)
    required = auth_policy.is_mfa_required(policy, user.role)
    if user.mfa_enabled and otp_code:
        _verify_code(session, user, otp_code, vault, policy, client_ip, user_agent)
        return _issue(
            session, user, provider, subject, source_revision,
            policy.session_ttl_seconds, client_ip, user_agent,
        )
    if user.mfa_enabled or required:
        binding = store.load(session, identity_bindings.key(provider, subject))
        challenge = identity_flows.create(session, vault, "mfa", {
            "provider": provider, "subject": subject, "source_revision": source_revision,
            "user_id": user.id, "user_revision": revision(user),
            "binding_revision": binding["created_at"],
        })
        return LoginResult(
            user=user, challenge=challenge,
            requirement="mfa_required" if user.mfa_enabled else "mfa_setup_required",
        )
    return _issue(
        session, user, provider, subject, source_revision,
        policy.session_ttl_seconds, client_ip, user_agent,
    )


def challenge_user(
    session: Session, vault: CredentialVault, token: str,
) -> tuple[User, dict[str, Any]]:
    _, flow = identity_flows.read(session, vault, "mfa", token)
    identity_sources.ensure_current(session, flow["provider"], flow["source_revision"])
    binding = store.load(session, identity_bindings.key(flow["provider"], flow["subject"]))
    user = session.scalar(select(User).where(User.id == flow["user_id"]).with_for_update()
                          .execution_options(populate_existing=True))
    if (
        user is None or not user.enabled or auth._is_locked(user, store.utcnow())
        or flow["user_revision"] != revision(user)
        or not binding or binding.get("user_id") != user.id
        or binding.get("created_at") != flow["binding_revision"]
    ):
        raise IdentityAuthenticationError("身份验证流程已失效，请重新登录")
    return user, flow


def _verify_code(
    session: Session, user: User, code: str, vault: CredentialVault,
    policy: auth_policy.AuthPolicy, client_ip: str | None, user_agent: str | None,
) -> None:
    from src.services import mfa

    try:
        mfa.verify_login_code(session, user, code, vault)
    except (mfa.MfaInvalidError, mfa.MfaRequiredError) as error:
        auth._record_failed_attempt(
            user, store.utcnow(), policy.login_failure_threshold, policy.lock_duration_seconds,
        )
        _audit(session, user, False, "invalid_mfa", client_ip, user_agent)
        store.commit(session)
        raise IdentityAuthenticationError("验证码错误或已过期") from error


def verify_challenge(
    session: Session, settings: Settings, vault: CredentialVault, token: str,
    code: str, client_ip: str | None, user_agent: str | None,
) -> LoginResult:
    store.lock(session)
    user, flow = challenge_user(session, vault, token)
    if not user.mfa_enabled:
        raise IdentitySourceError("请先完成 MFA 绑定")
    policy = auth_policy.resolve(session, settings)
    _verify_code(session, user, code, vault, policy, client_ip, user_agent)
    _consume(session, token)
    return _issue(
        session, user, flow["provider"], flow["subject"], flow["source_revision"],
        policy.session_ttl_seconds, client_ip, user_agent,
    )


def _consume(session: Session, token: str) -> None:
    from src.models.platform_setting import PlatformSetting

    row = session.get(PlatformSetting, f"identity.flow.mfa.{identity_flows.token_hash(token)}")
    if row is None:
        raise IdentityAuthenticationError("身份验证流程已使用")
    session.delete(row)


def enroll(
    session: Session, settings: Settings, vault: CredentialVault, token: str,
    code: str, client_ip: str | None, user_agent: str | None,
) -> tuple[LoginResult, list[str]]:
    store.lock(session)
    user, flow = challenge_user(session, vault, token)
    if user.mfa_enabled:
        raise IdentitySourceError("账号已绑定 MFA，请验证后登录")
    policy = auth_policy.resolve(session, settings)
    try:
        recovery_codes = auth.confirm_mfa(session, user, code, vault)
    except auth.MfaInvalidError as error:
        auth._record_failed_attempt(
            user, store.utcnow(), policy.login_failure_threshold, policy.lock_duration_seconds,
        )
        _audit(session, user, False, "invalid_mfa", client_ip, user_agent)
        store.commit(session)
        raise IdentityAuthenticationError("验证码错误或已过期") from error
    _consume(session, token)
    store.audit(session, "mfa", "mfa_enabled", user.username, user.id, None, {
        "mfa_enabled": True,
    })
    result = _issue(
        session, user, flow["provider"], flow["subject"], flow["source_revision"],
        policy.session_ttl_seconds, client_ip, user_agent,
    )
    return result, recovery_codes
