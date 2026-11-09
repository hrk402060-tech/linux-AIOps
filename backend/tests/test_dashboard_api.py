from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.asset import (
    Asset, AssetEnvironment, AssetLifecycleStatus, AssetStatus, OsFamily,
)
from src.models.alert import Alert, AlertConnector
from src.models.incident import Incident
from src.models.cmdb import CmdbApplication, CmdbBusiness, CmdbService, CmdbServiceAsset
from src.models.knowledge import KnowledgeRelease, KnowledgeSyncRun
from src.models.monitoring import MonitoringAlertState, MonitoringPolicy, MonitoringSnapshot
from src.models.probes import HttpProbe, HttpProbeRun
from src.models.scan_task import ScanStatus, ScanTask, ScanType
from src.models.vulnerability import VulnerabilityFinding


def test_dashboard_returns_empty_operational_snapshot(client: TestClient) -> None:
    response = client.get("/api/v1/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["assets"] == {
        "total": 0, "online": 0, "offline": 0, "unknown": 0, "disabled": 0,
    }
    assert body["vulnerabilities"]["total"] == 0
    assert body["scans"]["success_rate"] == 0
    assert body["knowledge"]["active_version"] is None
    assert body["knowledge"]["source_count"] == 9
    assert body["knowledge"]["healthy_source_count"] == 0
    assert body["knowledge"]["never_synced_source_count"] == 9
    assert body["knowledge"]["freshness"] == "未同步"
    assert body["recent_scans"] == []
    assert body["affected_assets"] == []
    assert body["monitoring_mode"] == "ssh_agentless_near_realtime"
    assert body["monitoring_assets"] == []
    assert body["catalog"] == {
        "businesses": 0, "applications": 0, "services": 0, "apis": 0,
        "linked_assets": 0, "unlinked_assets": 0,
    }
    assert body["service_health"]["summary"] == {
        "total": 0, "healthy": 0, "degraded": 0,
        "unavailable": 0, "unobserved": 0,
    }
    assert body["operations"] == {
        "active_alerts": 0, "severe_alerts": 0,
        "active_incidents": 0, "severe_incidents": 0,
        "running_tasks": 0, "failed_tasks": 0, "enabled_schedules": 0,
        "active_probes": 0, "failing_probes": 0, "unobserved_probes": 0,
        "pending_verification": 0,
    }
    assert body["attention_items"] == []


def test_dashboard_aggregates_risk_and_recent_activity(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    assets = [
        Asset(
            id=str(uuid4()), name="edge-01", host="192.0.2.10", ssh_port=22,
            os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
            group_name="edge", credential_id=None, labels=[], status=AssetStatus.ONLINE,
        ),
        Asset(
            id=str(uuid4()), name="db-01", host="192.0.2.11", ssh_port=22,
            os_family=OsFamily.KYLIN, environment=AssetEnvironment.PRODUCTION,
            group_name="database", credential_id=None, labels=[], status=AssetStatus.OFFLINE,
        ),
    ]
    release = KnowledgeRelease(
        id=str(uuid4()), version="2026.09.03", name="production", package_id="pkg-1",
        source_name="offline bundle", signature_key_id="trusted-1",
        content_sha256="a" * 64, advisory_count=120, status="active", activated_at=now,
    )
    tasks = [
        _task("task-completed", "nightly scan", ScanStatus.COMPLETED, now, 2),
        _task("task-failed", "edge retry", ScanStatus.FAILED, now, 0),
        _task("task-queued", "queued scan", ScanStatus.QUEUED, now, 0),
    ]
    findings = [
        _finding(tasks[0], assets[0], "CVE-2026-2001", "critical", "confirmed", now),
        _finding(tasks[0], assets[0], "CVE-2026-2002", "high", "manual_confirmation", now),
    ]
    database_session.add_all([
        *assets, release, *tasks, *findings,
        MonitoringPolicy(asset_id=assets[0].id),
        MonitoringPolicy(asset_id=assets[1].id),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=assets[0].id, status="online", read_only=True,
            metrics={"cpu_percent": 91, "memory_percent": 70, "disk_percent": 64},
            collected_at=now,
        ),
        MonitoringSnapshot(
            id=str(uuid4()), asset_id=assets[1].id, status="offline", read_only=True,
            metrics={}, error_message="unreachable", collected_at=now,
        ),
        MonitoringAlertState(
            id=str(uuid4()), asset_id=assets[0].id, metric="cpu_percent",
            status="triggered", last_value=91, threshold=85, triggered_at=now,
        ),
        KnowledgeSyncRun(
            id=str(uuid4()), source_id=None, source_name="CNVD", mode="offline_package",
            status="completed", filename="cnvd.zip", signature_key_id="trusted-1",
            content_sha256="b" * 64, advisory_count=120, message="导入成功",
            started_at=now, finished_at=now,
        ),
    ])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["assets"] == {
        "total": 2, "online": 1, "offline": 1, "unknown": 0, "disabled": 0,
    }
    assert body["vulnerabilities"] == {
        "total": 2, "critical": 1, "high": 1, "medium": 0, "low": 0,
        "confirmed": 1, "manual_confirmation": 1, "pending": 2,
    }
    assert body["scans"] == {
        "total": 3, "queued": 1, "running": 0, "completed": 1, "failed": 1,
        "success_rate": 50,
    }
    assert body["knowledge"]["active_version"] == "2026.09.03"
    assert body["knowledge"]["last_sync_status"] == "completed"
    assert body["knowledge"]["source_count"] == 9
    assert body["knowledge"]["healthy_source_count"] == 1
    assert body["knowledge"]["never_synced_source_count"] == 8
    assert body["knowledge"]["freshness"] == "未完整同步"
    assert body["recent_scans"][0]["finding_count"] == 2
    assert body["affected_assets"] == [
        {"asset_id": assets[0].id, "asset_name": "edge-01", "total": 2, "critical": 1, "high": 1}
    ]
    assert body["resource_alerts"] == {"cpu": 1, "memory": 0, "disk": 0}
    assert body["monitoring_assets"][0]["asset_name"] == "edge-01"
    assert body["monitoring_assets"][0]["active_alerts"] == 1
    assert body["active_alerts"][0]["metric"] == "cpu_percent"


def test_dashboard_includes_current_cmdb_service_context(
    client: TestClient, database_session: Session
) -> None:
    asset = Asset(
        id=str(uuid4()), name="api-01", host="192.0.2.30", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="api", credential_id=None, labels=[], status=AssetStatus.ONLINE,
    )
    business = CmdbBusiness(id=str(uuid4()), code="PAY", name="支付平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="PAY-APP", name="支付应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="PAY-API",
        name="支付 API", service_type="api",
    )
    relation = CmdbServiceAsset(
        id=str(uuid4()), service_id=service.id, asset_id=asset.id,
        relation_role="primary",
    )
    database_session.add_all([asset, business, application, service, relation])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    context = body["monitoring_assets"][0]["cmdb_services"]
    assert context == [{
        "business_id": business.id, "business_name": "支付平台",
        "application_id": application.id, "application_name": "支付应用",
        "service_id": service.id, "service_name": "支付 API",
        "service_type": "api", "relation_role": "primary",
    }]
    assert body["catalog"] == {
        "businesses": 1, "applications": 1, "services": 1, "apis": 0,
        "linked_assets": 1, "unlinked_assets": 0,
    }
    assert body["service_health"]["summary"] == {
        "total": 1, "healthy": 0, "degraded": 0,
        "unavailable": 0, "unobserved": 1,
    }
    assert body["service_health"]["services"][0]["service_name"] == "支付 API"


def test_dashboard_excludes_archived_assets_from_catalog_links(
    client: TestClient, database_session: Session
) -> None:
    asset = Asset(
        id=str(uuid4()), name="archived-01", host="192.0.2.90", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="archive", credential_id=None, labels=[],
        status=AssetStatus.OFFLINE, lifecycle_status=AssetLifecycleStatus.ARCHIVED,
    )
    business = CmdbBusiness(id=str(uuid4()), code="OLD", name="历史业务")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="OLD-APP", name="历史应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="OLD-SVC",
        name="历史服务", service_type="other",
    )
    relation = CmdbServiceAsset(
        id=str(uuid4()), service_id=service.id, asset_id=asset.id,
        relation_role="member",
    )
    database_session.add_all([asset, business, application, service, relation])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["catalog"]["linked_assets"] == 0
    assert body["catalog"]["unlinked_assets"] == 0


def test_dashboard_aggregates_platform_attention_queue(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset = Asset(
        id=str(uuid4()), name="ops-01", host="192.0.2.80", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="ops", credential_id=None, labels=[], status=AssetStatus.ONLINE,
    )
    connector = AlertConnector(
        id=str(uuid4()), name="zabbix-prod", source_type="zabbix",
        encrypted_secret=b"cipher", nonce=b"0" * 12, key_version="v1",
        secret_hint="abcd", operator="admin",
    )
    alert = Alert(
        id=str(uuid4()), connector_id=connector.id, external_id="problem-1",
        title="数据库连接耗尽", description="连接池达到上限", severity="critical",
        status="open", asset_id=asset.id, asset_name=asset.name,
        sanitized_payload={}, received_at=now, updated_at=now,
    )
    incident = Incident(
        id=str(uuid4()), title="支付接口不可用", description="探测连续失败",
        severity="high", status="in_progress", asset_id=asset.id,
        asset_name=asset.name, operator="admin", trace_id=str(uuid4()),
        created_at=now, updated_at=now,
    )
    business = CmdbBusiness(id=str(uuid4()), code="PAY", name="支付平台")
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id, code="PAY-APP", name="支付应用",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id, code="PAY-API",
        name="支付 API", service_type="api",
    )
    probe = HttpProbe(
        id=str(uuid4()), name="支付 API", service_id=service.id,
        asset_id=asset.id, url="http://192.0.2.80/health", method="HEAD",
        expected_status=200, timeout_seconds=3, status="active",
        operator_username="admin", created_at=now,
    )
    run = HttpProbeRun(
        id=str(uuid4()), probe_id=probe.id, business_id=business.id,
        application_id=application.id, service_id=service.id, asset_id=asset.id,
        status="error", latency_ms=3000, error_code="timeout",
        config_snapshot={}, operator_username="admin", trace_id=str(uuid4()),
        checked_at=now,
    )
    failed_scan = _task("task-failed-platform", "生产巡检", ScanStatus.FAILED, now, 0)
    database_session.add_all([
        asset, business, application, service, connector, alert, incident,
        probe, run, failed_scan,
        MonitoringAlertState(
            id=str(uuid4()), asset_id=asset.id, metric="memory_percent",
            status="triggered", last_value=94, threshold=90, triggered_at=now,
        ),
    ])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["active_alerts"] == 2
    assert body["operations"]["severe_alerts"] == 1
    assert body["operations"]["active_incidents"] == 1
    assert body["operations"]["severe_incidents"] == 1
    assert body["operations"]["failed_tasks"] == 1
    assert body["operations"]["active_probes"] == 1
    assert body["operations"]["failing_probes"] == 1
    assert {item["target"] for item in body["attention_items"]} >= {
        "alerts", "incidents", "probes", "automation",
    }


def test_dashboard_uses_latest_probe_result(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset, business, application, service = _probe_context("latest")
    probe = HttpProbe(
        id=str(uuid4()), name="订单 API", service_id=service.id,
        asset_id=asset.id, url="http://192.0.2.81/health", method="HEAD",
        expected_status=200, timeout_seconds=3, status="active",
        operator_username="admin", created_at=now,
    )
    runs = [
        _probe_run(
            probe, business, application, service, "error",
            now - timedelta(minutes=5),
        ),
        _probe_run(probe, business, application, service, "healthy", now),
    ]
    database_session.add_all([asset, business, application, service, probe, *runs])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["active_probes"] == 1
    assert body["operations"]["failing_probes"] == 0
    assert all(item["kind"] != "probe" for item in body["attention_items"])


def test_dashboard_excludes_archived_failed_scan(
    client: TestClient, database_session: Session
) -> None:
    task = _task(
        "archived-failed", "已归档失败扫描", ScanStatus.FAILED,
        datetime.now(timezone.utc), 0,
    )
    task.archived = True
    database_session.add(task)
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["failed_tasks"] == 0
    assert body["scans"]["failed"] == 0
    assert all(item["id"] != task.id for item in body["attention_items"])


def test_dashboard_excludes_disabled_asset_from_current_operations(
    client: TestClient, database_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    asset, business, application, service = _probe_context("disabled")
    asset.lifecycle_status = AssetLifecycleStatus.DISABLED
    probe = HttpProbe(
        id=str(uuid4()), name="停用服务", service_id=service.id,
        asset_id=asset.id, url="http://192.0.2.81/health", method="HEAD",
        expected_status=200, timeout_seconds=3, status="active",
        operator_username="admin", created_at=now,
    )
    run = _probe_run(probe, business, application, service, "error", now)
    monitoring_alert = MonitoringAlertState(
        id=str(uuid4()), asset_id=asset.id, metric="cpu_percent",
        status="triggered", last_value=98, threshold=85, triggered_at=now,
    )
    database_session.add_all([
        asset, business, application, service, probe, run, monitoring_alert,
    ])
    database_session.commit()

    body = client.get("/api/v1/dashboard").json()

    assert body["operations"]["active_probes"] == 0
    assert body["operations"]["active_alerts"] == 0
    assert body["monitoring_assets"][0]["active_alerts"] == 0
    assert body["resource_alerts"] == {"cpu": 0, "memory": 0, "disk": 0}
    assert body["attention_items"] == []


def _probe_context(
    suffix: str,
) -> tuple[Asset, CmdbBusiness, CmdbApplication, CmdbService]:
    asset = Asset(
        id=str(uuid4()), name=f"probe-{suffix}", host="192.0.2.81", ssh_port=22,
        os_family=OsFamily.OPENEULER, environment=AssetEnvironment.PRODUCTION,
        group_name="api", credential_id=None, labels=[], status=AssetStatus.ONLINE,
    )
    business = CmdbBusiness(
        id=str(uuid4()), code=f"BIZ-{suffix}", name=f"业务-{suffix}",
    )
    application = CmdbApplication(
        id=str(uuid4()), business_id=business.id,
        code=f"APP-{suffix}", name=f"应用-{suffix}",
    )
    service = CmdbService(
        id=str(uuid4()), application_id=application.id,
        code=f"SVC-{suffix}", name=f"服务-{suffix}", service_type="api",
    )
    return asset, business, application, service


def _probe_run(
    probe: HttpProbe,
    business: CmdbBusiness,
    application: CmdbApplication,
    service: CmdbService,
    status: str,
    checked_at: datetime,
) -> HttpProbeRun:
    return HttpProbeRun(
        id=str(uuid4()), probe_id=probe.id, business_id=business.id,
        application_id=application.id, service_id=service.id,
        asset_id=probe.asset_id, status=status, latency_ms=20,
        error_code="timeout" if status == "error" else None,
        config_snapshot={}, operator_username="admin", trace_id=str(uuid4()),
        checked_at=checked_at,
    )


def _task(
    task_id: str, name: str, status: ScanStatus, created_at: datetime, finding_count: int
) -> ScanTask:
    return ScanTask(
        id=task_id, name=name, scan_type=ScanType.COMPREHENSIVE, status=status, progress=100,
        read_only=True, asset_ids=[], asset_snapshot=[], knowledge_release_id=None,
        knowledge_version=None, rerun_of_id=None, result_summary={"total_findings": finding_count},
        findings=[{} for _ in range(finding_count)], execution_log=[], created_at=created_at,
    )


def _finding(
    task: ScanTask, asset: Asset, cve_id: str, severity: str, conclusion: str, detected_at: datetime
) -> VulnerabilityFinding:
    return VulnerabilityFinding(
        id=str(uuid4()), scan_task_id=task.id, scan_task_name=task.name, asset_id=asset.id,
        asset_name=asset.name, knowledge_version=None, cve_id=cve_id, advisory_id=cve_id,
        title=cve_id, severity=severity, package_name="kernel", installed_version="1",
        fixed_version="2", source_name="vendor", vendor_url=None, conclusion=conclusion,
        confidence="high" if conclusion == "confirmed" else "low", evidence_chain=[],
        detected_at=detected_at,
    )
