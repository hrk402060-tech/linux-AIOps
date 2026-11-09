from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.cmdb import (
    CmdbApplication, CmdbBusiness, CmdbLifecycle, CmdbService,
    CmdbServiceAsset, ServiceAssetStatus,
)
from src.services.cmdb_context import snapshot_for_asset
from test_alerts_api import _asset


def seed_context(
    session: Session, asset_id: str,
) -> tuple[CmdbBusiness, CmdbApplication, CmdbService, CmdbServiceAsset]:
    business = CmdbBusiness(id=str(uuid4()), code=uuid4().hex, name="business")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="app", name="application",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="svc",
        name="service", service_type="web",
    )
    relation = CmdbServiceAsset(
        id=str(uuid4()), asset_id=asset_id, service_id=service.id,
        relation_role="primary",
    )
    for item in (business, application, service, relation):
        session.add(item)
        session.flush()
    session.commit()
    return business, application, service, relation


def test_snapshot_projects_only_safe_relation_fields(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client)
    business, application, service, _ = seed_context(database_session, asset["id"])
    result = snapshot_for_asset(database_session, asset["id"])
    assert result == {"services": [{
        "business_id": business.id, "business_name": "business",
        "application_id": application.id, "application_name": "application",
        "service_id": service.id, "service_name": "service",
        "service_type": "web", "relation_role": "primary",
    }], "truncated": False}
    assert snapshot_for_asset(database_session, "other-asset") == {
        "services": [], "truncated": False,
    }


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_snapshot_excludes_inactive_hierarchy(
    client: TestClient, database_session: Session, index: int,
) -> None:
    asset = _asset(client)
    items = seed_context(database_session, asset["id"])
    if index == 3:
        items[index].status = ServiceAssetStatus.UNLINKED
    else:
        items[index].lifecycle_status = CmdbLifecycle.DISABLED
    database_session.commit()
    assert snapshot_for_asset(database_session, asset["id"])["services"] == []


def test_snapshot_is_bounded_and_stable(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset(client)
    _, application, service, _ = seed_context(database_session, asset["id"])
    ids = [service.id]
    for index in range(51):
        item = CmdbService(
            id=str(uuid4()), application_id=application.id, code=f"svc-{index}",
            name="service", service_type="web",
        )
        database_session.add(item)
        database_session.flush()
        database_session.add(CmdbServiceAsset(
            id=str(uuid4()), asset_id=asset["id"], service_id=item.id,
        ))
        ids.append(item.id)
    database_session.commit()
    result = snapshot_for_asset(database_session, asset["id"])
    assert result["truncated"] is True
    assert [item["service_id"] for item in result["services"]] == sorted(ids)[:50]
