from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import logging
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.services.crypto import CredentialVault

logger = logging.getLogger(__name__)


class IdentitySourceError(ValueError):
    pass


class IdentityAuthenticationError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def lock(session: Session) -> None:
    from src.services import settings_repository

    settings_repository.lock(session)
    session.info["identity_write_lock"] = True


def load(session: Session, key: str) -> dict[str, Any] | None:
    query = select(PlatformSetting).where(PlatformSetting.key == key)
    if session.info.get("identity_write_lock"):
        query = query.with_for_update()
    item = session.scalar(query.execution_options(populate_existing=True))
    if item is None:
        return None
    try:
        value = json.loads(item.value)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError) as error:
        raise IdentitySourceError("身份源配置不可用，请联系管理员检查") from error


def put(
    session: Session, key: str, value: dict[str, Any], operator: str = "system",
) -> None:
    item = session.get(PlatformSetting, key)
    if item is None:
        item = PlatformSetting(key=key)
        session.add(item)
    item.value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    item.updated_by = operator
    item.updated_at = utcnow()


def commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.error("Identity transaction failed: %s", type(error).__name__)
        raise IdentitySourceError("身份源配置保存失败，请稍后重试") from error
    finally:
        session.info.pop("identity_write_lock", None)


def seal(vault: CredentialVault, key: str, value: str) -> dict[str, str]:
    ciphertext, nonce = vault.encrypt(value, key, "identity_source")
    return {
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "key_version": vault.key_version,
    }


def unseal(vault: CredentialVault, key: str, value: dict[str, str]) -> str:
    try:
        return vault.decrypt(
            base64.b64decode(value["ciphertext"], validate=True),
            base64.b64decode(value["nonce"], validate=True),
            key, "identity_source", value["key_version"],
        )
    except (KeyError, ValueError, TypeError, InvalidTag) as error:
        raise IdentitySourceError("身份源密钥无法解密，请检查平台主密钥") from error


def audit(
    session: Session, provider: str, action: str, operator: str,
    user_id: str | None, before: dict[str, Any] | None, after: dict[str, Any],
) -> None:
    session.add(CmdbChangeAudit(
        id=str(uuid4()), entity_type="identity_source", entity_id=provider,
        action=action, operator_username=operator, operator_user_id=user_id,
        trace_id=str(uuid4()), before_snapshot=before, after_snapshot=after,
    ))


def authentication_settings(session: Session) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "local_login_enabled": True, "ldap_enabled": False,
        "oidc_enabled": False, "new_account_policy": "admin_only",
    }
    value = load(session, "settings.authentication")
    if value is None:
        return defaults
    from src.schemas.platform_settings import AuthenticationSettings

    try:
        return AuthenticationSettings.model_validate(value).model_dump()
    except ValueError as error:
        raise IdentitySourceError("登录方式配置无效，请联系管理员修复") from error


def ensure_enabled(session: Session, provider: str) -> None:
    if not authentication_settings(session).get(f"{provider}_enabled", False):
        raise IdentitySourceError("该外部身份源尚未启用")
