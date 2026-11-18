from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.schemas.identity import LdapConfiguration, OidcConfiguration, Provider
from src.services.crypto import CredentialVault
from src.services import identity_store as store
from src.services.identity_store import IdentitySourceError as IdentitySourceError

SECRET_FIELDS = {"ldap": "bind_password", "oidc": "client_secret"}


def key(provider: str) -> str:
    return f"identity.source.{provider}"


def validate(provider: Provider, payload: dict[str, Any]) -> dict[str, Any]:
    schema = LdapConfiguration if provider == "ldap" else OidcConfiguration
    try:
        parsed = schema.model_validate(payload)
    except ValidationError as error:
        messages = "; ".join(
            f"{'.'.join(str(item) for item in issue['loc'])}: {issue['msg']}"
            for issue in error.errors(include_input=False, include_url=False)
        )
        raise IdentitySourceError(messages) from error
    configuration = parsed.model_dump()
    secret_name = SECRET_FIELDS[provider]
    secret = getattr(parsed, secret_name)
    configuration[secret_name] = secret.get_secret_value() if secret else None
    return configuration


def load_configuration(
    session: Session, vault: CredentialVault, provider: Provider,
) -> dict[str, Any]:
    saved = store.load(session, key(provider))
    if saved is None:
        raise IdentitySourceError("请先保存该身份源的连接配置")
    configuration = dict(saved["configuration"])
    configuration[SECRET_FIELDS[provider]] = store.unseal(vault, key(provider), saved["secret"])
    return configuration


def _draft(
    session: Session, vault: CredentialVault, provider: Provider, payload: dict[str, Any],
) -> dict[str, Any]:
    configuration = validate(provider, payload)
    secret_name = SECRET_FIELDS[provider]
    if not configuration.get(secret_name):
        configuration[secret_name] = load_configuration(session, vault, provider)[secret_name]
    return configuration


def read(session: Session, provider: Provider) -> dict[str, Any]:
    from src.services.identity_bindings import has_verified_admin

    saved = store.load(session, key(provider))
    if saved is None:
        configuration = (
            LdapConfiguration.model_construct(server_url="", base_dn="", bind_dn="")
            if provider == "ldap"
            else OidcConfiguration.model_construct(issuer_url="", client_id="", redirect_uri="")
        ).model_dump(exclude={SECRET_FIELDS[provider]})
        return {
            **configuration, "secret_configured": False, "tested_at": None,
            "test_success": False, "verified_admin_login": False, "updated_at": None,
            "revision": "initial",
        }
    row = session.get(store.PlatformSetting, key(provider))
    return {
        **saved["configuration"], "secret_configured": bool(saved.get("secret")),
        "tested_at": saved.get("tested_at"), "test_success": saved.get("test_success", False),
        "verified_admin_login": has_verified_admin(session, provider),
        "updated_at": row.updated_at.isoformat() if row else None,
        "revision": saved["revision"],
    }


def save(
    session: Session, vault: CredentialVault, provider: Provider, payload: dict[str, Any],
    operator: str, user_id: str | None, expected_revision: str | None = None,
) -> dict[str, Any]:
    store.lock(session)
    before = read(session, provider)
    if expected_revision is not None and expected_revision.strip('"') != before["revision"]:
        raise IdentitySourceError("身份源已被其他管理员修改，请重新加载后保存")
    configuration = _draft(session, vault, provider, payload)
    previous = store.load(session, key(provider))
    if previous and configuration == load_configuration(session, vault, provider):
        return before
    active = store.authentication_settings(session).get(f"{provider}_enabled", False)
    if active:
        _ensure_source_can_change(session, provider)
        _probe(provider, configuration)
    secret_name = SECRET_FIELDS[provider]
    secret = configuration.pop(secret_name)
    saved = {
        "configuration": configuration,
        "secret": store.seal(vault, key(provider), secret),
        "revision": str(uuid4()), "tested_at": store.utcnow().isoformat() if active else None,
        "test_success": bool(active),
    }
    store.put(session, key(provider), saved, operator)
    after = {
        **configuration, "secret_configured": True, "test_success": bool(active),
    }
    store.audit(session, provider, "configured", operator, user_id, before, after)
    store.commit(session)
    return read(session, provider)


def _ensure_source_can_change(session: Session, provider: Provider) -> None:
    from src.services.identity_bindings import has_verified_admin

    policy = store.authentication_settings(session)
    if not policy["local_login_enabled"] and not any(
        name != provider and policy.get(f"{name}_enabled") and has_verified_admin(session, name)
        for name in ("ldap", "oidc")
    ):
        raise IdentitySourceError("修改唯一外部登录源前，请先启用本地登录或验证另一个管理员登录源")


def _probe(provider: Provider, configuration: dict[str, Any]) -> str:
    try:
        if provider == "ldap":
            from src.services.identity_ldap import test_connection
        else:
            from src.services.identity_oidc import test_connection
        return test_connection(configuration)
    except ImportError as error:
        raise IdentitySourceError("身份源依赖尚未安装，请先更新后端依赖") from error


def test(
    session: Session, vault: CredentialVault, provider: Provider,
    payload: dict[str, Any], operator: str, user_id: str | None,
) -> dict[str, Any]:
    configuration = _draft(session, vault, provider, payload)
    started = time.monotonic()
    success = True
    try:
        message = _probe(provider, configuration)
    except IdentitySourceError as error:
        success, message = False, str(error)
    checked_at = store.utcnow().isoformat()
    store.lock(session)
    saved = store.load(session, key(provider))
    if saved and configuration == load_configuration(session, vault, provider):
        saved.update(tested_at=checked_at, test_success=success)
        store.put(session, key(provider), saved, operator)
    store.audit(
        session, provider, "connection_test", operator, user_id, None,
        {"success": success, "checked_at": checked_at},
    )
    store.commit(session)
    return {
        "success": success, "message": message, "checked_at": checked_at,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def validate_authentication_settings(
    session: Session, values: dict[str, Any],
) -> None:
    from src.services.identity_bindings import has_verified_admin

    store.lock(session)
    providers = [name for name in ("ldap", "oidc") if values.get(f"{name}_enabled")]
    local_enabled = bool(values.get("local_login_enabled", True))
    if not local_enabled and not providers:
        raise IdentitySourceError("至少保留一种可用的登录方式")
    for provider in providers:
        saved = store.load(session, key(provider))
        if not saved or not saved.get("test_success"):
            raise IdentitySourceError("请先保存并成功测试将启用的外部身份源")
    if not local_enabled and not any(
        has_verified_admin(session, provider) for provider in providers
    ):
        raise IdentitySourceError("请先使用已关联的管理员账号完成外部登录，再停用本地登录")


def current_revision(session: Session, provider: Provider) -> str:
    saved = store.load(session, key(provider))
    if saved is None:
        raise IdentitySourceError("身份源配置不存在")
    return str(saved["revision"])


def ensure_current(session: Session, provider: Provider, revision: str) -> None:
    store.ensure_enabled(session, provider)
    if current_revision(session, provider) != revision:
        raise IdentitySourceError("身份源配置已更新，请重新开始登录")
