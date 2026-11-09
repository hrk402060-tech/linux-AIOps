from collections.abc import Iterable
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetLifecycleStatus
from src.models.asset_access import UserAssetAccess
from src.models.auth import User

if TYPE_CHECKING:
    from src.auth import AuthPrincipal

SYSTEM_OPERATORS = frozenset({"system", "scheduler", "break-glass-admin"})


class AssetAuthorizationError(PermissionError):
    """Raised when a queued operation loses its current asset authorization."""


def require_asset_management_scope(
    session: Session, principal: "AuthPrincipal"
) -> None:
    if principal.role == "admin":
        return
    user = session.get(User, principal.user_id) if principal.user_id else None
    if user is None or not user.enabled or user.asset_scope != "all":
        raise HTTPException(status_code=403, detail="仅全资产操作员可以管理服务器资产")


def require_background_assets(
    session: Session,
    operator: str | None,
    user_id: str | None,
    asset_ids: Iterable[str],
) -> None:
    """Re-read the requester's current account and asset grants before collection."""
    if user_id is None and operator in SYSTEM_OPERATORS:
        return
    if not user_id or not operator:
        raise AssetAuthorizationError("任务执行身份已失效")
    session.expire_all()
    user = session.get(User, user_id)
    if (
        user is None
        or user.username != operator
        or not user.enabled
        or user.must_change_password
        or user.role not in {"admin", "operator"}
    ):
        raise AssetAuthorizationError("任务执行权限已撤销")
    allowed = None if user.role == "admin" or user.asset_scope == "all" else set(
        session.scalars(select(UserAssetAccess.asset_id).where(
            UserAssetAccess.user_id == user.id,
            UserAssetAccess.can_view.is_(True),
        ))
    )
    if not all_assets_visible(asset_ids, allowed):
        raise AssetAuthorizationError("任务执行权限已撤销")
    enabled = set(session.scalars(select(Asset.id).where(
        Asset.id.in_(asset_ids),
        Asset.lifecycle_status == AssetLifecycleStatus.ENABLED,
    )))
    if enabled != set(asset_ids):
        raise AssetAuthorizationError("任务目标资产已停用或归档")


def visible_asset_ids(
    session: Session, principal: "AuthPrincipal"
) -> set[str] | None:
    if principal.role == "admin":
        return None
    user = session.get(User, principal.user_id) if principal.user_id else None
    if user is None or not user.enabled:
        return set()
    if user.asset_scope == "all":
        return None
    return set(session.scalars(select(UserAssetAccess.asset_id).where(
        UserAssetAccess.user_id == user.id,
        UserAssetAccess.can_view.is_(True),
    )))


def all_assets_visible(
    asset_ids: Iterable[str], allowed: set[str] | frozenset[str] | None,
) -> bool:
    if allowed is None:
        return True
    targets = set(asset_ids)
    return bool(targets) and targets.issubset(allowed)


def evidence_asset_ids(
    asset_ids: Iterable[str], asset_snapshot: Iterable[dict[str, object]],
    evidence: Iterable[dict[str, object]] = (),
) -> set[str]:
    targets = set(asset_ids)
    targets.update(
        str(item["id"]) for item in asset_snapshot
        if isinstance(item, dict) and item.get("id")
    )
    targets.update(
        str(item["asset_id"]) for item in evidence
        if isinstance(item, dict) and item.get("asset_id")
    )
    return targets


def require_assets(
    session: Session, principal: "AuthPrincipal", asset_ids: Iterable[str],
) -> None:
    if principal.must_change_password:
        raise HTTPException(status_code=403, detail="必须先修改临时密码")
    if not all_assets_visible(asset_ids, visible_asset_ids(session, principal)):
        raise HTTPException(status_code=403, detail="当前账号未获全部目标资产的访问授权")


def require_asset(
    session: Session, principal: "AuthPrincipal", asset_id: str, ssh: bool = False
) -> None:
    if principal.must_change_password:
        raise HTTPException(status_code=403, detail="必须先修改临时密码")
    if principal.user_id:
        user = session.get(User, principal.user_id)
        if user is None or not user.enabled:
            raise HTTPException(status_code=403, detail="当前账号已停用")
    if ssh and principal.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="审计员不能建立或操作 SSH 会话")
    allowed = visible_asset_ids(session, principal)
    if allowed is None:
        return
    grant = session.scalar(select(UserAssetAccess).where(
        UserAssetAccess.user_id == principal.user_id,
        UserAssetAccess.asset_id == asset_id,
        UserAssetAccess.can_view.is_(True),
    ))
    if grant is None or (ssh and not grant.can_ssh):
        raise HTTPException(status_code=403, detail="当前账号未获该资产的访问授权")


def require_asset_mutation(
    session: Session,
    principal: "AuthPrincipal",
    asset_id: str,
    target_lifecycle: AssetLifecycleStatus | None = None,
) -> None:
    """Prevent non-admins from mutating archived assets or changing into archive."""
    require_asset(session, principal, asset_id)
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if principal.role != "admin" and (
        asset.lifecycle_status == AssetLifecycleStatus.ARCHIVED
        or target_lifecycle == AssetLifecycleStatus.ARCHIVED
    ):
        raise HTTPException(status_code=403, detail="仅管理员可以修改或归档已归档资产")

