from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbLifecycle, CmdbService,
    CmdbServiceAsset, ServiceAssetStatus,
)

MAX_CONTEXT_SERVICES = 50


def snapshot_for_asset(session: Session, asset_id: str) -> dict[str, object]:
    rows = session.execute(
        select(CmdbServiceAsset, CmdbService, CmdbApplication, CmdbBusiness)
        .join(CmdbService, CmdbService.id == CmdbServiceAsset.service_id)
        .join(CmdbApplication, CmdbApplication.id == CmdbService.application_id)
        .join(CmdbBusiness, CmdbBusiness.id == CmdbApplication.business_id)
        .where(
            CmdbServiceAsset.asset_id == asset_id,
            CmdbServiceAsset.status == ServiceAssetStatus.ACTIVE,
            CmdbService.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbApplication.lifecycle_status == CmdbLifecycle.ACTIVE,
            CmdbBusiness.lifecycle_status == CmdbLifecycle.ACTIVE,
        )
        .order_by(
            CmdbBusiness.name, CmdbApplication.name, CmdbService.name,
            CmdbBusiness.id, CmdbApplication.id, CmdbService.id, CmdbServiceAsset.id,
        )
        .limit(MAX_CONTEXT_SERVICES + 1)
    ).all()
    services = [
        {
            "business_id": business.id,
            "business_name": business.name,
            "application_id": application.id,
            "application_name": application.name,
            "service_id": service.id,
            "service_name": service.name,
            "service_type": service.service_type,
            "relation_role": relation.relation_role,
        }
        for relation, service, application, business in rows[:MAX_CONTEXT_SERVICES]
    ]
    return {"services": services, "truncated": len(rows) > MAX_CONTEXT_SERVICES}
