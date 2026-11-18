from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import secrets

from sqlalchemy.orm import Session

from src.config import Settings
from src.services import auth_policy, identity_flows, identity_ldap, identity_oidc
from src.services import identity_sessions, identity_sources, identity_store as store
from src.services.crypto import CredentialVault
from src.services.identity_store import IdentityAuthenticationError


def options(session: Session) -> dict[str, bool]:
    policy = store.authentication_settings(session)
    return {name: bool(policy[name]) for name in (
        "local_login_enabled", "ldap_enabled", "oidc_enabled",
    )}


def ldap_login(
    session: Session, settings: Settings, vault: CredentialVault,
    username: str, password: str, otp_code: str | None,
    client_ip: str | None, user_agent: str | None,
) -> identity_sessions.LoginResult:
    store.ensure_enabled(session, "ldap")
    _check_throttle(session, settings, username)
    configuration = identity_sources.load_configuration(session, vault, "ldap")
    source_revision = identity_sources.current_revision(session, "ldap")
    try:
        claims = identity_ldap.authenticate(configuration, username, password)
    except IdentityAuthenticationError:
        _failed_login(session, settings, username)
        raise
    result = identity_sessions.finish(
        session, settings, vault, "ldap", claims["subject"], source_revision,
        client_ip, user_agent, otp_code,
    )
    from src.models.platform_setting import PlatformSetting

    attempt = session.get(PlatformSetting, _attempt_key(username))
    if attempt is not None:
        session.delete(attempt)
        store.commit(session)
    return result


def _attempt_key(username: str) -> str:
    return f"identity.attempt.{sha256(username.strip().lower().encode()).hexdigest()}"


def _check_throttle(session: Session, settings: Settings, username: str) -> None:
    attempt = store.load(session, _attempt_key(username))
    if attempt and attempt.get("locked_until"):
        if datetime.fromisoformat(attempt["locked_until"]) > store.utcnow():
            raise IdentityAuthenticationError("账号尝试次数过多，请稍后重试")


def _failed_login(session: Session, settings: Settings, username: str) -> None:
    store.lock(session)
    policy = auth_policy.resolve(session, settings)
    key = _attempt_key(username)
    attempt = store.load(session, key) or {"count": 0}
    count = int(attempt["count"]) + 1
    locked = (
        (store.utcnow() + timedelta(seconds=policy.lock_duration_seconds)).isoformat()
        if count >= policy.login_failure_threshold else None
    )
    store.put(session, key, {"count": 0 if locked else count, "locked_until": locked})
    store.commit(session)


def oidc_start(
    session: Session, vault: CredentialVault,
) -> tuple[str, str]:
    store.ensure_enabled(session, "oidc")
    configuration = identity_sources.load_configuration(session, vault, "oidc")
    metadata = identity_oidc.discover(configuration)
    browser = secrets.token_urlsafe(48)
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    state = identity_flows.create(session, vault, "oidc", {
        "browser_hash": identity_flows.token_hash(browser), "nonce": nonce,
        "verifier": verifier, "revision": identity_sources.current_revision(session, "oidc"),
    })
    return identity_oidc.authorization_url(
        configuration, metadata, state, nonce, verifier,
    ), browser


def oidc_callback(
    session: Session, settings: Settings, vault: CredentialVault,
    state: str, browser: str, code: str, provider_error: str | None,
    client_ip: str | None, user_agent: str | None,
) -> identity_sessions.LoginResult:
    flow = identity_flows.consume(
        session, vault, "oidc", state, identity_flows.token_hash(browser),
    )
    identity_sources.ensure_current(session, "oidc", flow["revision"])
    if provider_error or not code:
        raise IdentityAuthenticationError("外部身份源未批准登录，请重新尝试")
    configuration = identity_sources.load_configuration(session, vault, "oidc")
    claims = identity_oidc.exchange(
        configuration, identity_oidc.discover(configuration),
        code, flow["verifier"], flow["nonce"],
    )
    return identity_sessions.finish(
        session, settings, vault, "oidc", claims["subject"], flow["revision"],
        client_ip, user_agent,
    )
