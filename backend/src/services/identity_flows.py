from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import secrets
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.models.platform_setting import PlatformSetting
from src.services.crypto import CredentialVault
from src.services import identity_store as store
from src.services.identity_store import IdentitySourceError


def token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def create(
    session: Session, vault: CredentialVault, kind: str,
    payload: dict[str, Any], *, lifetime_seconds: int = 300,
) -> str:
    token = secrets.token_urlsafe(48)
    key = f"identity.flow.{kind}.{token_hash(token)}"
    store.lock(session)
    # These rows are challenges, not evidence; expired challenges must not accumulate.
    session.execute(delete(PlatformSetting).where(
        PlatformSetting.key.startswith("identity.flow."),
        PlatformSetting.updated_at < store.utcnow() - timedelta(minutes=15),
    ))
    stored = {
        **payload, "expires_at": (store.utcnow() + timedelta(seconds=lifetime_seconds)).isoformat(),
    }
    store.put(session, key, {"sealed": store.seal(vault, key, json.dumps(stored))})
    store.commit(session)
    return token


def read(
    session: Session, vault: CredentialVault, kind: str, token: str,
) -> tuple[str, dict[str, Any]]:
    if not token or len(token) > 128:
        raise IdentitySourceError("身份验证流程已过期，请重新登录")
    key = f"identity.flow.{kind}.{token_hash(token)}"
    stored = store.load(session, key)
    if stored is None:
        raise IdentitySourceError("身份验证流程已过期，请重新登录")
    try:
        value = json.loads(store.unseal(vault, key, stored["sealed"]))
        if datetime.fromisoformat(value["expires_at"]) <= store.utcnow():
            raise ValueError
        return key, value
    except (ValueError, KeyError, TypeError) as error:
        raise IdentitySourceError("身份验证流程已过期，请重新登录") from error


def consume(
    session: Session, vault: CredentialVault, kind: str, token: str,
    browser_hash: str | None = None,
) -> dict[str, Any]:
    store.lock(session)
    key, payload = read(session, vault, kind, token)
    if browser_hash is not None and not secrets.compare_digest(
        str(payload.get("browser_hash", "")), browser_hash,
    ):
        raise IdentitySourceError("登录浏览器校验失败，请重新开始登录")
    result = session.execute(delete(PlatformSetting).where(PlatformSetting.key == key))
    if result.rowcount != 1:
        raise IdentitySourceError("登录挑战已使用，请重新开始登录")
    store.commit(session)
    return payload
