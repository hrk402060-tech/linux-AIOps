from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.auth import AuthSession, LoginAudit, User
from src.services import mfa
from src.services.auth_sessions import (
    list_active_sessions as list_active_sessions,
    revoke_session_by_id as revoke_session_by_id,
)

password_hash = PasswordHash.recommended()
dummy_password_hash = password_hash.hash("sentinel-dummy-password-value")


class AuthenticationError(Exception):
    pass


class MfaRequiredError(Exception):
    pass


class MfaSetupRequiredError(Exception):
    pass


class MfaInvalidError(Exception):
    pass


class UserConflictError(Exception):
    pass


def normalize_username(username: str) -> str:
    return username.strip().lower()


def create_user(
    session: Session, username: str, password: str, role: str,
    display_name: str | None = None, must_change_password: bool = False,
    asset_scope: str = "all", commit: bool = True,
) -> User:
    normalized = normalize_username(username)
    if session.scalar(select(User).where(User.username == normalized)) is not None:
        raise UserConflictError("用户名已存在")
    user = User(
        username=normalized,
        display_name=display_name.strip() if display_name else None,
        password_hash=password_hash.hash(password),
        role=role,
        asset_scope=asset_scope,
        must_change_password=must_change_password,
    )
    session.add(user)
    if commit:
        session.commit()
        session.refresh(user)
    else:
        session.flush()
    return user


def ensure_bootstrap_admin(session: Session, username: str, password: str) -> None:
    if not username or not password:
        return
    if session.scalar(select(User.id).limit(1)) is not None:
        return
    create_user(session, username, password, "admin", "初始管理员", True)


def authenticate(
    session: Session, username: str, password: str, client_ip: str | None,
    user_agent: str | None, ttl_seconds: int, lock_threshold: int,
    lock_seconds: int, otp_code: str | None = None, vault: object | None = None,
    require_mfa: bool = False,
) -> tuple[User, str]:
    now = datetime.now(timezone.utc)
    normalized = normalize_username(username)
    user = session.scalar(select(User).where(User.username == normalized).with_for_update())
    if user is None:
        password_hash.verify(password, dummy_password_hash)
        _record_login(
            session, None, normalized, False, "invalid_credentials",
            client_ip, user_agent,
        )
        raise AuthenticationError
    if not user.enabled or _is_locked(user, now):
        _record_login(session, user, normalized, False, _failure_reason(user, now), client_ip, user_agent)
        raise AuthenticationError
    valid_password, updated_hash = password_hash.verify_and_update(
        password, user.password_hash
    )
    if not valid_password:
        _record_failed_attempt(user, now, lock_threshold, lock_seconds)
        _record_login(session, user, normalized, False, "invalid_credentials", client_ip, user_agent)
        raise AuthenticationError
    if updated_hash is not None:
        user.password_hash = updated_hash
    if require_mfa and not user.mfa_enabled:
        _record_login(session, user, normalized, False, "mfa_setup_required", client_ip, user_agent)
        raise MfaSetupRequiredError
    if user.mfa_enabled:
        if vault is None:
            raise MfaInvalidError
        try:
            mfa.verify_login_code(session, user, otp_code, vault)
        except mfa.MfaRequiredError as exc:
            _record_login(session, user, normalized, False, "mfa_required", client_ip, user_agent)
            raise MfaRequiredError from exc
        except mfa.MfaInvalidError as exc:
            _record_failed_attempt(user, now, lock_threshold, lock_seconds)
            _record_login(session, user, normalized, False, "invalid_mfa", client_ip, user_agent)
            raise MfaInvalidError from exc
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    raw_token = secrets.token_urlsafe(48)
    session.add(AuthSession(
        token_hash=hash_token(raw_token),
        user_id=user.id,
        expires_at=now + timedelta(seconds=ttl_seconds),
        client_ip=client_ip,
        user_agent=user_agent[:512] if user_agent else None,
    ))
    _record_login(session, user, normalized, True, "success", client_ip, user_agent)
    session.commit()
    session.refresh(user)
    return user, raw_token


def get_session_user(session: Session, raw_token: str) -> tuple[AuthSession, User] | None:
    now = datetime.now(timezone.utc)
    item = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    if item is None or item.revoked_at is not None or _as_utc(item.expires_at) <= now:
        return None
    user = session.get(User, item.user_id)
    if user is None or not user.enabled:
        return None
    if now - _as_utc(item.last_used_at) >= timedelta(minutes=5):
        item.last_used_at = now
        session.commit()
    return item, user


def revoke_session(session: Session, raw_token: str) -> None:
    item = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    )
    if item is not None and item.revoked_at is None:
        item.revoked_at = datetime.now(timezone.utc)
        session.commit()


def revoke_user_sessions(session: Session, user_id: str, commit: bool = True) -> None:
    session.execute(
        update(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        ).values(revoked_at=datetime.now(timezone.utc))
    )
    if commit:
        session.commit()


def reset_password(
    session: Session, user: User, password: str, must_change_password: bool,
    commit: bool = True,
) -> User:
    user.password_hash = password_hash.hash(password)
    user.must_change_password = must_change_password
    user.failed_login_attempts = 0
    user.locked_until = None
    revoke_user_sessions(session, user.id, commit=False)
    if commit:
        session.commit()
        session.refresh(user)
    return user


def unlock_user(session: Session, user: User, commit: bool = True) -> User:
    user.failed_login_attempts = 0
    user.locked_until = None
    if commit:
        session.commit()
        session.refresh(user)
    return user


def find_user_mfa_status(session: Session, user_id: str) -> bool:
    user = session.get(User, user_id)
    if user is None:
        raise ValueError("用户不存在")
    return user.mfa_enabled


def setup_mfa(session: Session, user: User, vault: object) -> dict[str, str | bool]:
    return mfa.setup(session, user, vault)


def confirm_mfa(
    session: Session, user: User, code: str, vault: object,
) -> list[str]:
    try:
        return mfa.confirm(session, user, code, vault)
    except mfa.MfaInvalidError as error:
        raise MfaInvalidError from error


def reset_mfa(session: Session, user: User) -> None:
    mfa.reset(session, user)


def change_own_password(
    session: Session, user: User, current_password: str, new_password: str,
    current_token_hash: str,
) -> User:
    if not password_hash.verify(current_password, user.password_hash):
        raise AuthenticationError
    if password_hash.verify(new_password, user.password_hash):
        raise ValueError("新密码不能与当前密码相同")
    user.password_hash = password_hash.hash(new_password)
    user.must_change_password = False
    session.execute(
        update(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.token_hash != current_token_hash,
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=datetime.now(timezone.utc))
    )
    session.commit()
    session.refresh(user)
    return user


def hash_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def _record_login(
    session: Session, user: User | None, username: str, success: bool,
    reason: str, client_ip: str | None, user_agent: str | None,
) -> None:
    session.add(LoginAudit(
        user_id=user.id if user else None,
        username=username,
        success=success,
        reason=reason,
        client_ip=client_ip,
        user_agent=user_agent[:512] if user_agent else None,
    ))
    session.commit()


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and _as_utc(user.locked_until) > now


def _record_failed_attempt(
    user: User, now: datetime, threshold: int, lock_seconds: int,
) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= threshold:
        user.locked_until = now + timedelta(seconds=lock_seconds)
        user.failed_login_attempts = 0


def _failure_reason(user: User | None, now: datetime) -> str:
    if user is None:
        return "invalid_credentials"
    if not user.enabled:
        return "disabled"
    if _is_locked(user, now):
        return "locked"
    return "invalid_credentials"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
