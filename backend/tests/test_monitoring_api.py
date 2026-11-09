from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.services.scan_engine import ScanCollectionError


METRICS = {
    "cpu_percent": 92.5, "load_1m": 3.2, "load_5m": 2.8, "load_15m": 2.1,
    "memory_percent": 71.0, "memory_used_bytes": 7_100, "memory_total_bytes": 10_000,
    "swap_percent": 12.0, "swap_used_bytes": 120, "swap_total_bytes": 1_000,
    "disk_percent": 83.0, "inode_percent": 61.0,
    "disks": [{"mount": "/", "used_percent": 83.0, "inode_percent": 61.0}],
    "network_rx_bytes": 1000, "network_tx_bytes": 2000, "uptime_seconds": 86400,
    "processes": [{"pid": "1", "name": "systemd", "cpu_percent": 0.1, "memory_percent": 0.2}],
    "systemd_services": [{"name": "sshd.service", "state": "running"}],
    "containers": [{"name": "nginx", "image": "nginx:1.27", "status": "Up 2 hours"}],
}


def _asset(client: TestClient) -> str:
    credential = client.post("/api/v1/credentials", json={
        "name": "Monitor reader", "credential_type": "password",
        "username": "readonly", "secret": "never-return-monitor-secret",
    }).json()
    response = client.post("/api/v1/assets", json={
        "name": "monitor-node-01", "host": "10.0.0.21", "ssh_port": 22,
        "os_family": "openeuler", "environment": "production",
        "group_name": "核心业务", "credential_id": credential["id"], "labels": [],
    })
    return response.json()["id"]


def test_collects_read_only_snapshot_and_exposes_trends(client: TestClient) -> None:
    asset_id = _asset(client)
    with patch("src.services.monitoring.monitor_collector.collect_metrics", return_value=METRICS):
        response = client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "online"
    assert body["metrics"]["cpu_percent"] == 92.5
    assert body["read_only"] is True
    assert "never-return-monitor-secret" not in response.text
    overview = client.get("/api/v1/monitoring/overview").json()
    assert overview["assets"][0]["latest_snapshot"]["metrics"]["uptime_seconds"] == 86400
    trends = client.get(f"/api/v1/monitoring/assets/{asset_id}/snapshots").json()
    assert len(trends) == 1


def test_threshold_duration_recovery_and_maintenance_window(client: TestClient) -> None:
    asset_id = _asset(client)
    policy = client.patch(f"/api/v1/monitoring/assets/{asset_id}/policy", json={
        "cpu_threshold": 80, "memory_threshold": 90, "disk_threshold": 90,
        "inode_threshold": 90, "load_threshold": 8, "duration_minutes": 0,
        "recovery_notify": True,
    })
    assert policy.status_code == 200
    with patch("src.services.monitoring.monitor_collector.collect_metrics", return_value=METRICS):
        client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    events = client.get("/api/v1/monitoring/events").json()
    assert events[0]["event_type"] == "triggered"
    assert events[0]["metric"] == "cpu_percent"
    alerts = client.get("/api/v1/alerts?asset_id=" + asset_id).json()
    assert alerts["total"] == 1
    assert alerts["items"][0]["source_type"] == "platform_monitoring"
    assert alerts["items"][0]["status"] == "open"

    recovered = {**METRICS, "cpu_percent": 20.0, "disk_percent": 20.0}
    with patch("src.services.monitoring.monitor_collector.collect_metrics", return_value=recovered):
        client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    events = client.get("/api/v1/monitoring/events").json()
    assert events[0]["event_type"] == "recovered"
    recovered_alert = client.get("/api/v1/alerts?asset_id=" + asset_id).json()["items"][0]
    assert recovered_alert["status"] == "resolved"
    assert recovered_alert["notification_summary"]["diagnostic_results"] >= 0

    now = datetime.now(timezone.utc)
    client.patch(f"/api/v1/monitoring/assets/{asset_id}/policy", json={
        "maintenance_start": (now - timedelta(minutes=5)).isoformat(),
        "maintenance_end": (now + timedelta(minutes=5)).isoformat(),
    })
    with patch("src.services.monitoring.monitor_collector.collect_metrics", return_value=METRICS):
        client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    assert client.get("/api/v1/monitoring/events").json()[0]["event_type"] == "suppressed"


def test_offline_state_is_audited_and_recovers(client: TestClient) -> None:
    asset_id = _asset(client)
    client.patch(
        f"/api/v1/monitoring/assets/{asset_id}/policy",
        json={"duration_minutes": 0, "recovery_notify": True},
    )
    with patch(
        "src.services.monitoring.monitor_collector.collect_metrics",
        side_effect=ScanCollectionError("connection failed"),
    ):
        response = client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    assert response.status_code == 201
    assert response.json()["status"] == "offline"
    assert "connection failed" not in response.text
    assert client.get("/api/v1/monitoring/events").json()[0]["metric"] == "availability"

    with patch("src.services.monitoring.monitor_collector.collect_metrics", return_value=METRICS):
        client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    event = next(
        item for item in client.get("/api/v1/monitoring/events").json()
        if item["metric"] == "availability" and item["event_type"] == "recovered"
    )
    assert event["event_type"] == "recovered"
    assert event["message"] == "服务器连接已恢复"
