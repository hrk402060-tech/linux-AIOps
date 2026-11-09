import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.asset_access import UserAccessAudit, UserAssetAccess
from src.models.auth import User
from src.schemas.asset_access import AssetAccessEntry, UserAccessResponse, UserAccessUpdate
from src.services import auth

if TYPE_CHECKING:
    from src.auth import AuthPrincipal

logger = logging.getLogger(__name__)


def snapshot(user: User) -> dict[str, object]:
    return {
        "role": user.role, "enabled": user.enabled, "asset_scope": user.asset_scope,
        "must_change_password": user.must_change_password,
    }


def record_change(
    session: Session, user: User, actor: "AuthPrincipal", action: str,
    before: dict[str, object] | None, after: dict[str, object] | None,
    reason: str,
) -> None:
    session.add(UserAccessAudit(
        user_id=user.id, username=user.username, action=action,
        before_snapshot=before, after_snapshot=after,
        operator_user_id=actor.user_id, operator_username=actor.username,
        reason=reason,
    ))


def commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("User management transaction failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="用户权限保存失败，请刷新后重试") from exc


def update_user(
    session: Session, user_id: str, changes: dict[str, object], actor: "AuthPrincipal"
) -> User:
    # Serialize administrator demotions so two admins cannot remove the last one.
    admins = list(session.scalars(select(User).where(User.role == "admin")
                                 .order_by(User.id).with_for_update()
                                 .execution_options(populate_existing=True)))
    user = find_user(session, user_id, locked=True)
    removing = changes.get("enabled") is False or (
        "role" in changes and changes["role"] != "admin"
    )
    if removing and user.id == actor.user_id:
        raise HTTPException(status_code=409, detail="不能停用当前账号或移除自身管理员角色")
    if removing and user.role == "admin" and user.enabled:
        if sum(item.enabled for item in admins) <= 1:
            raise HTTPException(status_code=409, detail="系统必须保留至少一个启用的管理员")
    before = snapshot(user)
    for key, value in changes.items():
        setattr(user, key, value.strip() if key == "display_name" and value else value)
    if user.role == "auditor":
        session.execute(update(UserAssetAccess).where(
            UserAssetAccess.user_id == user.id
        ).values(can_ssh=False))
    user.access_revision += 1
    auth.revoke_user_sessions(session, user.id, commit=False)
    record_change(session, user, actor, "user_updated", before, snapshot(user),
                  "管理员更新用户信息并撤销既有会话")
    commit(session)
    session.refresh(user)
    return user


def find_user(session: Session, user_id: str, locked: bool = False) -> User:
    statement = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    user = session.scalar(statement.with_for_update() if locked else statement)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def read_access(session: Session, user: User) -> UserAccessResponse:
    entries = session.scalars(select(UserAssetAccess).where(
        UserAssetAccess.user_id == user.id,
        UserAssetAccess.can_view.is_(True),
    ).order_by(UserAssetAccess.asset_id)).all()
    return UserAccessResponse(
        user_id=user.id, asset_scope="all" if user.role == "admin" else user.asset_scope,
        revision=user.access_revision,
        entries=[AssetAccessEntry(asset_id=item.asset_id, can_ssh=item.can_ssh)
                 for item in entries],
    )


def replace_access(
    session: Session, user_id: str, payload: UserAccessUpdate, actor: "AuthPrincipal"
) -> UserAccessResponse:
    user = find_user(session, user_id, locked=True)
    if user.role == "admin":
        raise HTTPException(status_code=409, detail="管理员拥有全部资产权限；请先调整角色")
    if payload.confirmation != user.username:
        raise HTTPException(status_code=409, detail="确认文本不匹配，请输入完整用户名")
    if user.access_revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="权限已被其他管理员修改，请刷新后重试")
    if user.role == "auditor" and any(item.can_ssh for item in payload.entries):
        raise HTTPException(status_code=409, detail="审计员只能查看资产，不能授予 SSH 权限")
    _validate_assets(session, payload)
    before = read_access(session, user).model_dump()
    _write_access(session, user, payload, actor)
    after = read_access(session, user).model_dump()
    record_change(session, user, actor, "asset_access_updated", before, after, payload.reason)
    commit(session)
    return UserAccessResponse.model_validate(after)


def _validate_assets(session: Session, payload: UserAccessUpdate) -> None:
    requested = {item.asset_id for item in payload.entries}
    found = set(session.scalars(select(Asset.id).where(
        Asset.id.in_(requested),
        Asset.lifecycle_status != AssetLifecycleStatus.ARCHIVED,
    )))
    if requested != found:
        raise HTTPException(status_code=404, detail="授权资产不存在或已归档，请刷新资产列表")


def _write_access(
    session: Session, user: User, payload: UserAccessUpdate, actor: "AuthPrincipal"
) -> None:
    result = session.execute(update(User).where(
        User.id == user.id, User.access_revision == payload.expected_revision,
    ).values(asset_scope=payload.asset_scope, access_revision=payload.expected_revision + 1))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="权限版本已变化，请刷新后重试")
    session.execute(delete(UserAssetAccess).where(UserAssetAccess.user_id == user.id))
    session.add_all([UserAssetAccess(
        user_id=user.id, asset_id=item.asset_id, can_view=True,
        can_ssh=item.can_ssh, granted_by_user_id=actor.user_id,
    ) for item in payload.entries])
    session.flush()
    session.refresh(user)
