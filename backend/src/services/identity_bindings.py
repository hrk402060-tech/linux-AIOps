from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.auth import User
from src.models.platform_setting import PlatformSetting
from src.schemas.identity import IdentityBindingCreate, IdentityInviteCreate, Provider
from src.services import auth, identity_store as store
from src.services.identity_store import IdentitySourceError


def key(provider: str, subject: str) -> str:
    return f"identity.binding.{provider}.{sha256(subject.encode()).hexdigest()}"


def authority(session: Session, provider: str) -> str:
    source = store.load(session, f"identity.source.{provider}")
    if source is None:
        raise IdentitySourceError("请先保存身份源配置")
    configuration = source["configuration"]
    names = ["issuer_url"] if provider == "oidc" else [
        "server_url", "base_dn", "subject_attribute",
    ]
    value = json.dumps({name: configuration[name] for name in names}, sort_keys=True)
    return sha256(value.encode()).hexdigest()


def list_bindings(session: Session, provider: Provider) -> list[dict[str, Any]]:
    query = select(PlatformSetting).where(
        PlatformSetting.key.startswith(f"identity.binding.{provider}."),
    ).order_by(PlatformSetting.updated_at.desc()).limit(500)
    if session.info.get("identity_write_lock"):
        query = query.with_for_update()
    rows = session.scalars(query.execution_options(populate_existing=True)).all()
    return [json.loads(row.value) for row in rows]


def has_verified_admin(
    session: Session, provider: str, exclude: str | None = None,
    exclude_user: str | None = None,
) -> bool:
    source = store.load(session, f"identity.source.{provider}")
    if source is None:
        return False
    for binding in list_bindings(session, provider):
        user_id = binding.get("user_id")
        if (
            not user_id or user_id == exclude_user or key(provider, binding["subject"]) == exclude
            or binding.get("verified_revision") != source["revision"]
            or binding.get("authority") != authority(session, provider)
        ):
            continue
        query = select(User).where(User.id == user_id)
        if session.info.get("identity_write_lock"):
            query = query.with_for_update()
        user = session.scalar(query.execution_options(populate_existing=True))
        if user and user.enabled and user.role == "admin":
            return True
    return False


def bind(
    session: Session, provider: Provider, payload: IdentityBindingCreate,
    operator: str, user_id: str | None,
) -> dict[str, Any]:
    store.lock(session)
    user = session.get(User, payload.user_id)
    if user is None or not user.enabled:
        raise IdentitySourceError("关联的平台账号不存在或已停用")
    binding_key = key(provider, payload.subject)
    if store.load(session, binding_key):
        raise IdentitySourceError("该外部身份已关联账号，请先移除原关联")
    binding = {
        "subject": payload.subject, "user_id": user.id, "username": user.username,
        "authority": authority(session, provider), "kind": "linked",
        "verified_revision": None, "created_at": store.utcnow().isoformat(),
    }
    store.put(session, binding_key, binding, operator)
    store.audit(session, provider, "account_linked", operator, user_id, None, {
        "user_id": user.id, "subject_hash": sha256(payload.subject.encode()).hexdigest(),
    })
    store.commit(session)
    return binding


def invite(
    session: Session, provider: Provider, payload: IdentityInviteCreate,
    operator: str, user_id: str | None,
) -> dict[str, Any]:
    store.lock(session)
    binding_key = key(provider, payload.subject)
    if store.load(session, binding_key) or session.scalar(select(User.id).where(
        User.username == auth.normalize_username(payload.username),
    )):
        raise IdentitySourceError("身份或用户名已存在，请直接关联现有平台账号")
    binding = {
        "subject": payload.subject, "user_id": None,
        "username": auth.normalize_username(payload.username),
        "display_name": payload.display_name, "role": payload.role,
        "authority": authority(session, provider), "kind": "invited",
        "expires_at": (store.utcnow() + timedelta(hours=payload.expires_hours)).isoformat(),
        "created_at": store.utcnow().isoformat(), "verified_revision": None,
    }
    store.put(session, binding_key, binding, operator)
    store.audit(session, provider, "account_invited", operator, user_id, None, {
        "username": binding["username"], "role": payload.role,
        "expires_at": binding["expires_at"],
    })
    store.commit(session)
    return binding


def resolve(session: Session, provider: Provider, subject: str) -> User:
    binding_key = key(provider, subject)
    binding = store.load(session, binding_key)
    if not binding or binding.get("authority") != authority(session, provider):
        raise IdentitySourceError("外部身份尚未关联平台账号，请联系管理员授权")
    if binding["kind"] == "invited":
        if (
            store.authentication_settings(session)["new_account_policy"] != "invite_only"
            or datetime.fromisoformat(binding["expires_at"]) <= store.utcnow()
        ):
            raise IdentitySourceError("账号邀请已失效或当前策略不允许邀请接入")
        try:
            user = auth.create_user(
                session, binding["username"], secrets.token_urlsafe(48), binding["role"],
                binding.get("display_name"), asset_scope="assigned", commit=False,
            )
        except auth.UserConflictError as error:
            raise IdentitySourceError("邀请用户名已被占用，请联系管理员重新关联") from error
        binding.update(kind="linked", user_id=user.id)
        store.put(session, binding_key, binding)
        # mark_verified reloads this binding; preserve the accepted invitation in this transaction.
        session.flush()
    user = session.get(User, binding["user_id"])
    if user is None:
        raise IdentitySourceError("关联的平台账号已不存在，请联系管理员")
    return user


def mark_verified(session: Session, provider: Provider, subject: str, revision: str) -> None:
    binding_key = key(provider, subject)
    binding = store.load(session, binding_key)
    if binding is None:
        raise IdentitySourceError("外部身份关联已移除，请重新登录")
    binding["verified_revision"] = revision
    binding["last_login_at"] = store.utcnow().isoformat()
    store.put(session, binding_key, binding)


def remove(
    session: Session, provider: Provider, subject: str,
    operator: str, operator_user_id: str | None,
) -> None:
    store.lock(session)
    binding_key = key(provider, subject)
    binding = store.load(session, binding_key)
    if binding is None:
        raise IdentitySourceError("身份关联不存在")
    policy = store.authentication_settings(session)
    if not policy["local_login_enabled"] and not any(
        has_verified_admin(session, name, binding_key if name == provider else None)
        for name in ("ldap", "oidc") if policy.get(f"{name}_enabled")
    ):
        raise IdentitySourceError("不能移除最后一个可用的外部管理员身份")
    if binding.get("user_id"):
        auth.revoke_user_sessions(session, binding["user_id"], commit=False)
    row = session.get(PlatformSetting, binding_key)
    if row is not None:
        session.delete(row)
    store.audit(session, provider, "account_unlinked", operator, operator_user_id, None, {
        "user_id": binding.get("user_id"), "subject_hash": sha256(subject.encode()).hexdigest(),
    })
    store.commit(session)
