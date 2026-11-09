from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.asset import (
    Asset, AssetEnvironment, AssetLifecycleStatus, AssetStatus, OsFamily,
)
from src.models.cmdb import CmdbApplication, CmdbBusiness
from src.models.monitoring import MonitoringSnapshot
from src.services.cmdb_discovery import preview


def _asset(name: str, lifecycle: AssetLifecycleStatus = AssetLifecycleStatus.ENABLED) -> Asset:
    return Asset(
        id=str(uuid4()),
        name=name,
        host=f"192.0.2.{len(name) + 10}",
        ssh_port=22,
        os_family=OsFamily.OPENEULER,
        environment=AssetEnvironment.TEST,
        group_name="测试",
        credential_id=None,
        labels=[],
        status=AssetStatus.ONLINE,
        lifecycle_status=lifecycle,
    )


def test_discovery_preview_uses_latest_snapshot_and_filters_inactive_assets(
    client: TestClient, database_session: Session,
) -> None:
    now = datetime.now(timezone.utc)
    enabled = _asset("enabled-node")
    disabled = _asset("disabled-node", AssetLifecycleStatus.DISABLED)
    archived = _asset("archived-node", AssetLifecycleStatus.ARCHIVED)
    database_session.add_all([enabled, disabled, archived])
    database_session.add_all([
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=enabled.id, status="online", read_only=True,
            metrics={"systemd_services": [{"name": "sshd", "state": "active"}]},
            collected_at=now - timedelta(minutes=5),
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=enabled.id, status="online", read_only=True,
            metrics={
                "systemd_services": [{"name": "nginx", "state": "running"}],
                "containers": [{"name": "web", "status": "Up 1 minute"}],
            },
            collected_at=now,
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=disabled.id, status="online", read_only=True,
            metrics={"systemd_services": [{"name": "hidden", "state": "active"}]},
            collected_at=now,
        ),
    ])
    database_session.commit()

    response = client.get("/api/v1/cmdb/discovery/preview")

    assert response.status_code == 200
    body = response.json()
    assert [item["asset_name"] for item in body["assets"]] == ["enabled-node"]
    assert body["assets"][0]["services"] == [
        {"name": "nginx", "source": "systemd", "state": "running"},
        {"name": "web", "source": "container", "state": "Up 1 minute"},
    ]


def test_discovery_preview_respects_asset_visibility(database_session: Session) -> None:
    asset = _asset("visible-node")
    database_session.add(asset)
    database_session.add(MonitoringSnapshot(
        id=str(uuid4()), asset_id=asset.id, status="online", read_only=True,
        metrics={"containers": [{"name": "api", "status": "running"}]},
        collected_at=datetime.now(timezone.utc),
    ))
    database_session.commit()

    result = preview(database_session, set())

    assert result["assets"] == []


def test_discovery_confirmation_creates_service_and_asset_relation(
    client: TestClient, database_session: Session,
) -> None:
    asset = _asset("confirm-node")
    business = CmdbBusiness(
        id=str(uuid4()), code="DISC-BIZ", name="发现业务",
        lifecycle_status="active",
    )
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="DISC-APP",
        name="发现应用", lifecycle_status="active",
    )
    database_session.add_all([asset, business, application])
    database_session.commit()

    payload = {
        "application_id": application.id,
        "asset_id": asset.id,
        "service_name": "nginx",
        "source": "systemd",
    }
    response = client.post("/api/v1/cmdb/discovery/confirm", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["created_service"] is True
    assert body["relation_status"] == "active"
    assert body["service_code"].startswith("DISC-")

    repeated = client.post("/api/v1/cmdb/discovery/confirm", json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["created_service"] is False
    assert repeated.json()["relation_id"] == body["relation_id"]
