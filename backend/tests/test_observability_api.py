from datetime import datetime, timezone
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.asset import Asset, AssetEnvironment, AssetStatus, OsFamily
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceAsset
from src.models.monitoring import MonitoringAlertState, MonitoringSnapshot


def test_service_observability_aggregates_current_runtime_context(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset = Asset(
        id=str(uuid4()), name="api-01", host="192.0.2.40", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="api", credential_id=None, labels=[], status=AssetStatus.ONLINE,
    )
    business = CmdbBusiness(id=str(uuid4()), code="ORDER", name="订单平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="ORDER-APP", name="订单应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="ORDER-API",
        name="订单 API", service_type="api",
    )
    database_session.add_all([
        asset, business, application, service,
        CmdbServiceAsset(
            id=str(uuid4()), service_id=service.id, asset_id=asset.id,
            relation_role="primary",
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=asset.id, status="online", read_only=True,
            metrics={"cpu_percent": 92, "memory_percent": 73, "disk_percent": 61},
            collected_at=now,
        ),
        MonitoringAlertState(
            id=str(uuid4()), asset_id=asset.id, metric="cpu_percent",
            status="triggered", last_value=92, threshold=85, triggered_at=now,
        ),
    ])
    database_session.commit()

    response = client.get("/api/v1/observability/services")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total": 1, "healthy": 0, "degraded": 1,
        "unavailable": 0, "unobserved": 0,
    }
    row = body["services"][0]
    assert row["service_id"] == service.id
    assert row["business_name"] == "订单平台"
    assert row["status"] == "degraded"
    assert row["freshness"] == "fresh"
    assert row["asset_count"] == 1
    assert row["online_assets"] == 1
    assert row["active_alerts"] == 1
    assert row["resource_peaks"]["cpu_percent"] == 92
    assert row["assets"][0]["asset_id"] == asset.id
    assert row["assets"][0]["freshness"] == "fresh"


def test_service_observability_marks_old_snapshot_stale_and_degraded(
    client: TestClient, database_session: Session
) -> None:
    collected_at = datetime.now(timezone.utc) - timedelta(hours=1)
    asset = Asset(
        id=str(uuid4()), name="stale-01", host="192.0.2.41", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="api", credential_id=None, status=AssetStatus.ONLINE,
    )
    business = CmdbBusiness(id=str(uuid4()), code="STALE", name="陈旧平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="STALE-APP", name="陈旧应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="STALE-API",
        name="陈旧 API", service_type="api",
    )
    database_session.add_all([
        asset, business, application, service,
        CmdbServiceAsset(
            id=str(uuid4()), service_id=service.id, asset_id=asset.id,
            relation_role="primary",
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=asset.id, status="online", read_only=True,
            metrics={"cpu_percent": 20}, collected_at=collected_at,
        ),
    ])
    database_session.commit()

    body = client.get("/api/v1/observability/services").json()

    row = body["services"][0]
    assert body["freshness_threshold_seconds"] == 900
    assert row["status"] == "degraded"
    assert row["freshness"] == "stale"
    assert row["stale_assets"] == 1
    assert row["assets"][0]["freshness"] == "stale"
    assert row["assets"][0]["collected_at"].startswith(collected_at.isoformat()[:19])


def test_service_observability_marks_service_without_assets_unobserved(
    client: TestClient, database_session: Session
) -> None:
    business = CmdbBusiness(id=str(uuid4()), code="OPS", name="运维平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="OPS-APP", name="运维应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="OPS-WORKER",
        name="任务服务", service_type="worker",
    )
    database_session.add_all([business, application, service])
    database_session.commit()

    body = client.get("/api/v1/observability/services?status=unobserved").json()

    assert body["summary"]["unobserved"] == 1
    assert body["services"][0]["service_name"] == "任务服务"
    assert body["services"][0]["assets"] == []
