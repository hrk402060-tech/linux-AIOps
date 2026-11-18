from datetime import datetime, timezone
from hashlib import sha256
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.auth import User
from src.models.cmdb import CmdbChangeAudit
from src.models.platform_setting import PlatformSetting
from src.schemas.access_policy import (
    AccessPolicyDocument, AccessPolicyResponse, FeatureCatalogItem,
)
from src.services import settings_repository
from src.services.auth_policy import AuthPolicyUnavailableError
from src.services.feature_permissions import FEATURES

POLICY_KEY = "auth.feature_access_policy"
MANAGED_PREFIX = "auth.feature_managed."
MAX_POLICY_BYTES = 512 * 1024
logger = logging.getLogger(__name__)


class AccessPolicyUnavailableError(AuthPolicyUnavailableError):
    pass


def _read(session: Session, locked: bool = False) -> tuple[AccessPolicyDocument, str]:
    try:
        item = settings_repository.record(session, POLICY_KEY) if locked else session.get(
            PlatformSetting, POLICY_KEY, populate_existing=True,
        )
        if item is None:
            return AccessPolicyDocument(roles=[], groups=[], users=[]), "initial"
        content = item.value.encode("utf-8")
        if len(content) > MAX_POLICY_BYTES:
            raise ValueError("Policy size exceeded")
        return AccessPolicyDocument.model_validate_json(content), sha256(content).hexdigest()
    except (SQLAlchemyError, ValueError) as error:
        logger.error("Feature access policy unavailable (%s)", type(error).__name__)
        raise AccessPolicyUnavailableError("功能授权策略不可用，请联系管理员") from error


def _response(document: AccessPolicyDocument, revision: str) -> AccessPolicyResponse:
    return AccessPolicyResponse(
        roles=document.roles, groups=document.groups, users=document.users,
        revision=revision,
        catalog=[FeatureCatalogItem(page=page, label=label, write=write)
                 for page, (label, write) in FEATURES.items()],
    )


def read_policy(session: Session) -> AccessPolicyResponse:
    return _response(*_read(session))


def _known_permission(permission: str) -> bool:
    page, _, action = permission.partition(":")
    feature = FEATURES.get(page)
    return feature is not None and (action == "read" or (action == "write" and feature[1]))


def effective_permissions(session: Session, user_id: str | None, role: str) -> frozenset[str] | None:
    if role == "admin":
        return None
    if role not in {"operator", "auditor"} or user_id is None:
        return frozenset()
    document, _ = _read(session)
    assignment = next((item for item in document.users if item.user_id == user_id), None)
    if assignment is None:
        try:
            marker = session.get(PlatformSetting, f"{MANAGED_PREFIX}{user_id}", populate_existing=True)
        except SQLAlchemyError as error:
            logger.error("Managed account access state unavailable (%s)", type(error).__name__)
            raise AccessPolicyUnavailableError("功能授权策略不可用，请联系管理员") from error
        return frozenset() if marker is not None else None
    role_ids = set(assignment.role_ids)
    groups = set(assignment.group_ids)
    for group in document.groups:
        if group.id in groups:
            role_ids.update(group.role_ids)
    permissions = set(assignment.permissions)
    for custom in document.roles:
        if custom.id in role_ids:
            permissions.update(custom.permissions)
    known = {item for item in permissions if _known_permission(item)}
    writes = {item for item in known if item.endswith(":write")}
    known.update(f"{item.removesuffix(':write')}:read" for item in writes)
    return frozenset(known - writes if role == "auditor" else known)


def _assert_accounts(
    session: Session, before: AccessPolicyDocument, after: AccessPolicyDocument,
) -> None:
    prior = {item.user_id for item in before.users}
    requested = {item.user_id for item in after.users}
    ids = sorted(prior | requested)
    account_roles: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        accounts = session.execute(select(User.id, User.role).where(
            User.id.in_(ids[start:start + 500]),
        ).order_by(User.id).with_for_update())
        account_roles.update({user_id: role for user_id, role in accounts})
    if any(account_roles.get(user_id) not in {"operator", "auditor"} for user_id in requested):
        raise ValueError("只能为现有的非管理员账号保存功能授权")
    if any(account_roles.get(user_id) in {"operator", "auditor"} for user_id in prior - requested):
        raise settings_repository.SettingsConflictError("已纳管账号不能恢复旧授权；请保留账号并清空所有授权")


def _encode(document: AccessPolicyDocument) -> str:
    content = document.model_dump_json()
    if len(content.encode("utf-8")) > MAX_POLICY_BYTES:
        raise ValueError("功能授权策略超出容量，请精简重复授权")
    return content


def _write(session: Session, content: str, operator: str) -> str:
    item = settings_repository.record(session, POLICY_KEY)
    if item is None:
        item = PlatformSetting(key=POLICY_KEY)
        session.add(item)
    item.value = content
    item.updated_by = operator
    item.updated_at = datetime.now(timezone.utc)
    return sha256(content.encode("utf-8")).hexdigest()


def _audit(
    session: Session, before: AccessPolicyDocument, after: AccessPolicyDocument,
    operator: str, operator_id: str | None,
) -> None:
    session.add(CmdbChangeAudit(
        id=str(uuid4()), entity_type="platform_setting", entity_id=POLICY_KEY,
        action="access_policy_updated", operator_user_id=operator_id,
        operator_username=operator, trace_id=str(uuid4()),
        before_snapshot=before.model_dump(), after_snapshot=after.model_dump(),
    ))


def save_policy(
    session: Session, document: AccessPolicyDocument, expected_revision: str,
    operator: str, operator_id: str | None,
) -> AccessPolicyResponse:
    content = _encode(document)
    all_permissions = {permission for item in [*document.roles, *document.users]
                       for permission in item.permissions}
    if not all(_known_permission(permission) for permission in all_permissions):
        raise ValueError("功能授权包含未知或不可委派的权限")
    try:
        settings_repository.lock(session)
        before, revision = _read(session, locked=True)
        if expected_revision.strip('"') != revision:
            raise settings_repository.SettingsConflictError("配置已被其他管理员修改，请重新加载后再保存")
        _assert_accounts(session, before, document)
        if before != document:
            revision = _write(session, content, operator)
            _audit(session, before, document, operator, operator_id)
        settings_repository.commit(session)
    except (SQLAlchemyError, ValueError, AccessPolicyUnavailableError):
        session.rollback()
        raise
    return _response(document, revision)


def initialize_user(session: Session, user_id: str) -> None:
    key = f"{MANAGED_PREFIX}{user_id}"
    try:
        if session.get(PlatformSetting, key) is None:
            marker = PlatformSetting(key=key, value="1")
            session.add(marker)
            session.flush([marker])
    except SQLAlchemyError as error:
        logger.error("Managed account creation failed (%s)", type(error).__name__)
        raise AccessPolicyUnavailableError("功能授权初始化失败，无法接入新账号") from error
