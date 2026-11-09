from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.auth import AuthPrincipal
from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceApi, CmdbServiceAsset,
    ServiceAssetStatus,
)
from src.services import asset_access


class CmdbAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class CmdbVisibility:
    asset_ids: frozenset[str] | None
    business_ids: frozenset[str] | None
    application_ids: frozenset[str] | None
    service_ids: frozenset[str] | None
    api_ids: frozenset[str] | None

    @property
    def unrestricted(self) -> bool:
        return self.asset_ids is None


def for_principal(session: Session, principal: AuthPrincipal) -> CmdbVisibility:
    return for_asset_ids(session, asset_access.visible_asset_ids(session, principal))


def for_asset_ids(session: Session, allowed: set[str] | None) -> CmdbVisibility:
    if allowed is None:
        return CmdbVisibility(None, None, None, None, None)
    asset_ids = frozenset(allowed)
    if not asset_ids:
        empty = frozenset()
        return CmdbVisibility(asset_ids, empty, empty, empty, empty)
    service_ids = frozenset(session.scalars(select(
        CmdbServiceAsset.service_id
    ).where(
        CmdbServiceAsset.asset_id.in_(asset_ids),
        CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE,
    )))
    application_ids = frozenset(session.scalars(select(
        CmdbService.application_id
    ).where(CmdbService.id.in_(service_ids))))
    business_ids = frozenset(session.scalars(select(
        CmdbApplication.business_id
    ).where(CmdbApplication.id.in_(application_ids))))
    api_ids = frozenset(session.scalars(select(
        CmdbServiceApi.id
    ).where(CmdbServiceApi.service_id.in_(service_ids))))
    return CmdbVisibility(asset_ids, business_ids, application_ids, service_ids, api_ids)


def allows(item: object, visibility: CmdbVisibility) -> bool:
    if visibility.unrestricted:
        return True
    if isinstance(item, CmdbBusiness):
        return item.id in (visibility.business_ids or ())
    if isinstance(item, CmdbApplication):
        return item.id in (visibility.application_ids or ())
    if isinstance(item, CmdbService):
        return item.id in (visibility.service_ids or ())
    if isinstance(item, CmdbServiceApi):
        return item.id in (visibility.api_ids or ())
    return False


def ensure_visible(item: object, visibility: CmdbVisibility) -> None:
    if not allows(item, visibility):
        raise CmdbAccessError("当前账号未获该 CMDB 记录关联资产的访问授权")


def ensure_asset_visible(asset_id: str, visibility: CmdbVisibility) -> None:
    if not visibility.unrestricted and asset_id not in (visibility.asset_ids or ()):
        raise CmdbAccessError("当前账号未获该资产的访问授权")


def ensure_root_create(visibility: CmdbVisibility) -> None:
    if not visibility.unrestricted:
        raise CmdbAccessError("指定资产账号不能创建无资产上下文的 CMDB 业务")


def ensure_writable(session: Session, item: object, visibility: CmdbVisibility) -> None:
    if visibility.unrestricted:
        return
    ensure_visible(item, visibility)
    statement = (
        select(CmdbServiceAsset.asset_id)
        .join(CmdbService, CmdbServiceAsset.service_id == CmdbService.id)
        .join(CmdbApplication, CmdbService.application_id == CmdbApplication.id)
    )
    if isinstance(item, CmdbBusiness):
        statement = statement.where(CmdbApplication.business_id == item.id)
    elif isinstance(item, CmdbApplication):
        statement = statement.where(CmdbApplication.id == item.id)
    elif isinstance(item, CmdbService):
        statement = statement.where(CmdbService.id == item.id)
    elif isinstance(item, CmdbServiceApi):
        statement = statement.where(CmdbService.id == item.service_id)
    else:
        raise CmdbAccessError("不支持修改该 CMDB 记录")
    # Historical references still restrict writes to shared metadata.
    related_ids = session.scalars(statement)
    if not asset_access.all_assets_visible(related_ids, visibility.asset_ids):
        raise CmdbAccessError("修改共享 CMDB 记录需要全部关联资产的访问授权")
