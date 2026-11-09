from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.alert import Alert, AlertConnector
from src.models.asset import (
    Asset, AssetEnvironment, AssetLifecycleStatus, AssetStatus, OsFamily,
)
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService
from src.models.monitoring import MonitoringAlertState
from src.models.probes import HttpProbe, HttpProbeRun
from src.models.scan_task import ScanStatus, ScanTask, ScanType


def test_dashboard_uses_latest_probe_result(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset, business, application, service, probe = _probe_context(now)
    old_run = _probe_run(
        probe, business, application, service, asset, "error",
        now - timedelta(minutes=1),
    )
    latest_run = _probe_run(
        probe, business, application, service, asset, "healthy", now
    )
    database_session.add_all([
        asset, business, application, service, probe, old_run, latest_run,
    ])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["active_probes"] == 1
    assert body["operations"]["failing_probes"] == 0
    assert not any(item["kind"] == "probe" for item in body["attention_items"])


def test_dashboard_excludes_archived_failed_scan(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    task = ScanTask(
        id="task-archived-failed", name="历史失败任务",
        scan_type=ScanType.COMPREHENSIVE, status=ScanStatus.FAILED, progress=100,
        read_only=True, asset_ids=[], asset_snapshot=[], knowledge_release_id=None,
        knowledge_version=None, rerun_of_id=None, result_summary={}, findings=[],
        execution_log=[], archived=True, created_at=now,
    )
    database_session.add(task)
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["failed_tasks"] == 0
    assert not any(item["id"] == task.id for item in body["attention_items"])


def test_dashboard_excludes_disabled_asset_alerts(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset = _asset("disabled-01", "192.0.2.82")
    asset.lifecycle_status = AssetLifecycleStatus.DISABLED
    connector = AlertConnector(
        id=str(uuid4()), name="disabled-source", source_type="zabbix",
        encrypted_secret=b"cipher", nonce=b"0" * 12, key_version="v1",
        secret_hint="abcd", operator="admin",
    )
    alert = Alert(
        id=str(uuid4()), connector_id=connector.id, external_id="disabled-alert",
        title="停用资产告警", description="不应进入当前工作台",
        severity="critical", status="open", asset_id=asset.id,
        asset_name=asset.name, sanitized_payload={}, received_at=now, updated_at=now,
    )
    monitoring = MonitoringAlertState(
        id=str(uuid4()), asset_id=asset.id, metric="cpu_percent",
        status="triggered", last_value=99, threshold=90, triggered_at=now,
    )
    database_session.add_all([asset, connector, alert, monitoring])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["active_alerts"] == 0
    assert body["operations"]["severe_alerts"] == 0
    assert not body["attention_items"]


def _probe_context(
    now: datetime,
) -> tuple[Asset, CmdbBusiness, CmdbApplication, CmdbService, HttpProbe]:
    asset = _asset("probe-01", "192.0.2.81")
    business = CmdbBusiness(id=str(uuid4()), code="OBS", name="可观测平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="OBS-APP", name="探测应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="OBS-SVC",
        name="健康检查", service_type="api",
    )
    probe = HttpProbe(
        id=str(uuid4()), name="健康检查", service_id=service.id,
        asset_id=asset.id, url="http://192.0.2.81/health", method="HEAD",
        expected_status=200, timeout_seconds=3, status="active",
        operator_username="admin", created_at=now,
    )
    return asset, business, application, service, probe


def _asset(name: str, host: str) -> Asset:
    return Asset(
        id=str(uuid4()), name=name, host=host, ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="ops", credential_id=None, labels=[], status=AssetStatus.ONLINE,
    )


def _probe_run(
    probe: HttpProbe,
    business: CmdbBusiness,
    application: CmdbApplication,
    service: CmdbService,
    asset: Asset,
    status: str,
    checked_at: datetime,
) -> HttpProbeRun:
    return HttpProbeRun(
        id=str(uuid4()), probe_id=probe.id, business_id=business.id,
        application_id=application.id, service_id=service.id, asset_id=asset.id,
        status=status, latency_ms=15, error_code=None, config_snapshot={},
        operator_username="admin", trace_id=str(uuid4()), checked_at=checked_at,
    )
