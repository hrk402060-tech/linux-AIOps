from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.services.identity_sources import validate_authentication_settings


def validate_authentication_policy(session: Session, normalized: dict[str, Any]) -> None:
    validate_authentication_settings(session, normalized)


def ensure_external_administrator_remains(
    session: Session, user_id: str, changes: dict[str, Any],
) -> None:
    from src.models.auth import User
    from src.services import identity_bindings, identity_store as store

    if not (
        changes.get("enabled") is False
        or ("role" in changes and changes["role"] != "admin")
    ):
        return
    store.lock(session)
    policy = store.authentication_settings(session)
    if policy["local_login_enabled"]:
        return
    user = session.get(User, user_id, populate_existing=True)
    if user is None or not user.enabled or user.role != "admin":
        return
    if not any(
        identity_bindings.has_verified_admin(session, provider, exclude_user=user_id)
        for provider in ("ldap", "oidc") if policy.get(f"{provider}_enabled")
    ):
        raise ValueError("不能停用或降级最后一个可通过外部身份源登录的管理员")
