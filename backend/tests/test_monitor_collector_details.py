from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.services import monitor_collector as collector
from src.services.scan_engine import ScanCollectionError
from test_monitoring_api import _asset


def _network(boot: str, uptime: float, rows: str) -> str:
    return f"{boot}\n{uptime} 0\nInter-| x\n face | x\n{rows}"


def test_collects_each_interface_rate_from_remote_double_sample() -> None:
    samples = [
        _network("boot-1", 100.0, "eth0: 100 0 0 0 0 0 0 0 200\nens1: 400 0 0 0 0 0 0 0 500"),
        _network("boot-1", 100.5, "eth0: 150 0 0 0 0 0 0 0 300\nens1: 500 0 0 0 0 0 0 0 700"),
    ]
    with patch.object(collector, "_execute", side_effect=samples), patch.object(
        collector.time, "sleep",
    ):
        metrics = collector._network_metrics(object())  # type: ignore[arg-type]
    assert metrics["network_rx_bytes"] == 650
    assert metrics["network_tx_bytes"] == 1000
    assert metrics["network_interfaces"] == [
        {"name": "eth0", "rx_bytes": 150, "tx_bytes": 300,
         "rx_bytes_per_second": 100.0, "tx_bytes_per_second": 200.0},
        {"name": "ens1", "rx_bytes": 500, "tx_bytes": 700,
         "rx_bytes_per_second": 200.0, "tx_bytes_per_second": 400.0},
    ]


@pytest.mark.parametrize("boot,uptime,received", [
    ("new-boot", 200.5, 150),
    ("boot-1", 99.0, 150),
    ("boot-1", 100.0, 150),
    ("boot-1", 100.5, 50),
    ("boot-1", 130.1, 150),
])
def test_restart_reset_and_invalid_interval_leave_rates_unknown(
    boot: str, uptime: float, received: int,
) -> None:
    samples = [
        _network("boot-1", 100.0, "eth0: 100 0 0 0 0 0 0 0 200"),
        _network(boot, uptime, f"eth0: {received} 0 0 0 0 0 0 0 300"),
    ]
    with patch.object(collector, "_execute", side_effect=samples), patch.object(
        collector.time, "sleep",
    ):
        metrics = collector._network_metrics(object())  # type: ignore[arg-type]
    interface = metrics["network_interfaces"][0]
    assert interface["rx_bytes_per_second"] is None
    assert interface["tx_bytes_per_second"] is None


def test_new_interface_has_no_fabricated_rate() -> None:
    samples = [
        _network("boot-1", 100.0, "lo: 100 0 0 0 0 0 0 0 200"),
        _network("boot-1", 100.5, "eth1: 500 0 0 0 0 0 0 0 900"),
    ]
    with patch.object(collector, "_execute", side_effect=samples), patch.object(
        collector.time, "sleep",
    ):
        metrics = collector._network_metrics(object())  # type: ignore[arg-type]
    interface = metrics["network_interfaces"][0]
    assert interface["name"] == "eth1"
    assert interface["rx_bytes_per_second"] is None


def test_parses_actual_partition_capacity_and_inode_counts() -> None:
    disks = collector._parse_disks(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda1 1000 800 180 80% /\n/dev/vdb1 2000 500 1400 25% /data pool",
        "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
        "/dev/vda1 100 40 60 40% /\n/dev/vdb1 500 100 400 20% /data pool",
    )
    assert [item["mount"] for item in disks] == ["/", "/data pool"]
    assert disks[0] == {
        "device": "/dev/vda1", "mount": "/", "total_bytes": 1024000,
        "used_bytes": 819200, "available_bytes": 184320, "used_percent": 80.0,
        "inode_total": 100, "inode_used": 40, "inode_available": 60,
        "inode_percent": 40.0, "inode_observed": True,
    }
    unknown = collector._parse_disks(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 1000 800 180 80% /",
        "Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/vda1 - - - - /",
    )[0]
    assert unknown["inode_observed"] is False
    assert unknown["inode_total"] is None


@pytest.mark.parametrize("value,expected", [("8\n", 8), ("0", None), ("bad", None), ("1.5", None)])
def test_remote_logical_cpu_count_is_validated(value: str, expected: int | None) -> None:
    assert collector._parse_cpu_count(value) == expected


@pytest.mark.parametrize("samples", [
    "cpu 100 0 100 800 0\ncpu 100 0 100 800 0",
    "cpu 100 0 100 800 0\ncpu 90 0 90 700 0",
    "cpu 100 0 100 800 0\ncpu nan 0 100 900 0",
    "cpu 100 0 100 800 0\ncpu inf 0 100 900 0",
    "cpu 100 0 100 800 0\ncpu 90 0 100 830 0",
])
def test_invalid_cpu_deltas_do_not_report_zero_usage(samples: str) -> None:
    with patch.object(collector, "_execute", return_value=samples):
        with pytest.raises(ScanCollectionError):
            collector._cpu_percent(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("counts", [
    "0 0 0 0%", "100 101 0 100%", "100 40 101 40%", "100 80 40 80%",
])
def test_invalid_inode_counts_are_unobserved(counts: str) -> None:
    disk = collector._parse_disks(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda1 1000 800 180 80% /",
        "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
        f"/dev/vda1 {counts} /",
    )[0]
    assert disk["inode_observed"] is False
    assert disk["inode_total"] is None
    assert disk["inode_used"] is None


def test_decodes_only_standard_mount_octal_escapes_once() -> None:
    disks = collector._parse_disks(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        r"/dev/mapper/data\040pool 1000 800 180 80% /data\040pool" "\n"
        r"/dev/vdb1 1000 800 180 80% /literal\134040\141\x20",
        "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
        r"/dev/mapper/data\040pool 100 40 60 40% /data\040pool",
    )
    assert disks[0]["device"] == "/dev/mapper/data pool"
    assert disks[0]["mount"] == "/data pool"
    assert disks[0]["inode_observed"] is True
    assert disks[1]["mount"] == r"/literal\040\141\x20"


def test_missing_disks_keep_explicit_unobserved_flags() -> None:
    with patch.object(collector, "_connect") as connect, patch.object(
        collector, "_execute", side_effect=[
            "0.1 0.1 0.1",
            "MemTotal: 1000 kB\nMemAvailable: 500 kB",
            "Filesystem 1024-blocks Used Available Capacity Mounted on",
            "Filesystem Inodes IUsed IFree IUse% Mounted on",
            "1000 0",
        ],
    ), patch.object(collector, "_cpu_percent", return_value=20), patch.object(
        collector, "_network_metrics", return_value={},
    ), patch.object(collector, "_execute_optional", return_value=""):
        metrics = collector.collect_metrics(
            object(), object(), "synthetic-test-material",  # type: ignore[arg-type]
        )
    assert metrics["disks"] == []
    assert metrics["disk_observed"] is False
    assert metrics["inode_observed"] is False
    connect.return_value.close.assert_called_once()


def test_collection_failure_does_not_return_sensitive_exception_text(client: TestClient) -> None:
    asset_id = _asset(client)
    sensitive_marker = "synthetic-private-key-or-password-material"
    with patch.object(
        collector, "collect_metrics",
        side_effect=ScanCollectionError("remote error " + sensitive_marker),
    ):
        response = client.post(f"/api/v1/monitoring/assets/{asset_id}/collect")
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "offline"
    assert sensitive_marker not in response.text
    assert "remote error" not in response.text


@pytest.mark.parametrize("error", [ValueError, OSError])
def test_connection_failure_does_not_echo_private_key_or_password(error: type[Exception]) -> None:
    sensitive_marker = "synthetic-private-key-or-password-material"
    with patch(
        "src.services.scan_engine.open_client", side_effect=error(sensitive_marker),
    ):
        with pytest.raises(ScanCollectionError) as caught:
            collector._connect(object(), object(), sensitive_marker)  # type: ignore[arg-type]
    assert sensitive_marker not in str(caught.value)
