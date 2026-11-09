from typing import TypeVar

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbLifecycle, CmdbService, CmdbServiceApi,
    CmdbServiceAsset, ServiceAssetStatus,
)
from src.models.probes import HttpProbe, HttpProbeRun

ProbeRecord = TypeVar("ProbeRecord", HttpProbe, HttpProbeRun)


class ProbeAccessError(PermissionError):
    pass


def require_context(
    session: Session, service_id: str, asset_id: str, api_id: str | None,
    visible_asset_ids: set[str] | None,
) -> None:
    if visible_asset_ids is None:
        return
    if asset_id not in visible_asset_ids:
        raise ProbeAccessError("当前账号未获该拨测资产的访问授权")
    service = session.get(CmdbService, service_id)
    application = session.get(CmdbApplication, service.application_id) if service else None
    business = session.get(CmdbBusiness, application.business_id) if application else None
    if not all(item and item.lifecycle_status == CmdbLifecycle.ACTIVE
               for item in (service, application, business)):
        raise ProbeAccessError("当前账号未获该拨测 CMDB 目录的访问授权")
    relation = session.scalar(select(CmdbServiceAsset.id).where(
        CmdbServiceAsset.service_id == service_id,
        CmdbServiceAsset.asset_id == asset_id,
        CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE,
    ))
    api_matches = api_id is None or session.scalar(select(CmdbServiceApi.id).where(
        CmdbServiceApi.id == api_id,
        CmdbServiceApi.service_id == service_id,
        CmdbServiceApi.lifecycle_status == CmdbLifecycle.ACTIVE,
    )) is not None
    if relation is None or not api_matches:
        raise ProbeAccessError("当前账号未获该拨测服务或 API 资产关系的访问授权")


def scope_context(
    statement: Select[tuple[ProbeRecord]], model: type[ProbeRecord],
    visible_asset_ids: set[str] | None,
) -> Select[tuple[ProbeRecord]]:
    if visible_asset_ids is None:
        return statement
    relation = select(CmdbServiceAsset.id).where(
        CmdbServiceAsset.service_id == model.service_id,
        CmdbServiceAsset.asset_id == model.asset_id,
        CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE,
    ).correlate(model).exists()
    api = select(CmdbServiceApi.id).where(
        CmdbServiceApi.id == model.api_id,
        CmdbServiceApi.service_id == model.service_id,
        CmdbServiceApi.lifecycle_status == CmdbLifecycle.ACTIVE,
    ).correlate(model).exists()
    hierarchy = select(CmdbService.id).join(
        CmdbApplication, CmdbService.application_id == CmdbApplication.id,
    ).join(
        CmdbBusiness, CmdbApplication.business_id == CmdbBusiness.id,
    ).where(
        CmdbService.id == model.service_id,
        CmdbService.lifecycle_status == CmdbLifecycle.ACTIVE,
        CmdbApplication.lifecycle_status == CmdbLifecycle.ACTIVE,
        CmdbBusiness.lifecycle_status == CmdbLifecycle.ACTIVE,
    ).correlate(model).exists()
    statement = statement.where(
        model.asset_id.in_(visible_asset_ids), relation, hierarchy,
        or_(model.api_id.is_(None), api),
    )
    if model is HttpProbeRun:
        # Historical evidence carries its own context, not the parent's current target.
        history_hierarchy = select(CmdbService.id).join(CmdbApplication).where(
            CmdbService.id == HttpProbeRun.service_id,
            CmdbService.application_id == HttpProbeRun.application_id,
            CmdbApplication.business_id == HttpProbeRun.business_id,
        ).correlate(HttpProbeRun).exists()
        statement = statement.where(history_hierarchy)
    return statement
