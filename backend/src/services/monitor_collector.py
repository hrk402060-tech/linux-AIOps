from __future__ import annotations

from typing import TYPE_CHECKING

from src.models.asset import Asset
from src.models.credential import Credential
from src.services.scan_engine import ScanCollectionError, _connect, _execute

OPTIONAL_OUTPUT_BYTES = 64 * 1024

if TYPE_CHECKING:
    import paramiko


def collect_metrics(
    asset: Asset, credential: Credential, secret: str,
    expected_host_key: paramiko.PKey | None = None,
) -> dict[str, object]:
    client = _connect(asset, credential, secret, expected_host_key)
    try:
        load = _numbers(_execute(client, "cat /proc/loadavg"), 3)
        memory = _parse_meminfo(_execute(client, "cat /proc/meminfo"))
        disks = _parse_disks(
            _execute(client, "df -P -x tmpfs -x devtmpfs"),
            _execute(client, "df -Pi -x tmpfs -x devtmpfs"),
        )
        return {
            "cpu_percent": _cpu_percent(client),
            "load_1m": load[0],
            "load_5m": load[1],
            "load_15m": load[2],
            **memory,
            "disk_percent": max((item["used_percent"] for item in disks), default=0.0),
            "inode_percent": max((item["inode_percent"] for item in disks), default=0.0),
            "disks": disks,
            **_parse_network(_execute(client, "cat /proc/net/dev")),
            "uptime_seconds": int(float(_execute(client, "cat /proc/uptime").split()[0])),
            "processes": _parse_processes(_execute_optional(
                client, "ps -eo pid=,comm=,%cpu=,%mem= --sort=-%cpu | head -n 21"
            )),
            "systemd_services": _parse_services(_execute_optional(
                client,
                "systemctl list-units --type=service --state=running,failed "
                "--no-pager --no-legend | head -n 101",
            )),
            "containers": _parse_containers(_execute_optional(
                client,
                "docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | head -n 101",
            )),
        }
    finally:
        client.close()


def _execute_optional(client: paramiko.SSHClient, command: str) -> str:
    _, stdout, _ = client.exec_command(command, timeout=10)
    output = stdout.read(OPTIONAL_OUTPUT_BYTES + 1)
    if len(output) > OPTIONAL_OUTPUT_BYTES or stdout.channel.recv_exit_status() != 0:
        return ""
    return output.decode("utf-8", errors="replace")


def _numbers(value: str, count: int) -> list[float]:
    try:
        items = [float(item) for item in value.split()[:count]]
    except (ValueError, IndexError) as exc:
        raise ScanCollectionError("监控指标格式无效") from exc
    if len(items) != count:
        raise ScanCollectionError("监控指标格式无效")
    return items


def _cpu_percent(client: paramiko.SSHClient) -> float:
    lines = _execute(
        client, "sh -c 'head -1 /proc/stat; sleep 0.2; head -1 /proc/stat'"
    ).splitlines()
    try:
        samples = [[float(item) for item in line.split()[1:]] for line in lines[:2]]
    except ValueError as exc:
        raise ScanCollectionError("CPU 指标格式无效") from exc
    if len(samples) != 2 or min(map(len, samples)) < 5:
        raise ScanCollectionError("CPU 指标格式无效")
    size = min(map(len, samples))
    first, second = samples[0][:size], samples[1][:size]
    total = sum(second) - sum(first)
    idle = (second[3] + second[4]) - (first[3] + first[4])
    return round(max(0.0, min(100.0, (total - idle) * 100 / total)), 1) if total else 0.0


def _parse_meminfo(value: str) -> dict[str, float | int]:
    data: dict[str, int] = {}
    for line in value.splitlines():
        key, separator, tail = line.partition(":")
        if separator:
            try:
                data[key] = int(tail.split()[0]) * 1024
            except (ValueError, IndexError):
                continue
    total = data.get("MemTotal", 0)
    available = data.get("MemAvailable", data.get("MemFree", 0))
    swap_total = data.get("SwapTotal", 0)
    swap_used = max(0, swap_total - data.get("SwapFree", 0))
    if not total:
        raise ScanCollectionError("内存指标格式无效")
    return {
        "memory_percent": round((total - available) * 100 / total, 1),
        "memory_used_bytes": total - available,
        "memory_total_bytes": total,
        "swap_percent": round(swap_used * 100 / swap_total, 1) if swap_total else 0.0,
        "swap_used_bytes": swap_used,
        "swap_total_bytes": swap_total,
    }


def _parse_disks(space: str, inodes: str) -> list[dict[str, object]]:
    inode_map = _df_percentages(inodes)
    items: list[dict[str, object]] = []
    for mount, percent in _df_percentages(space).items():
        items.append({"mount": mount, "used_percent": percent, "inode_percent": inode_map.get(mount, 0.0),
                      "inode_observed": mount in inode_map})
    return items


def _df_percentages(value: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in value.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 6 and fields[-2].endswith("%"):
            try:
                result[fields[-1]] = float(fields[-2][:-1])
            except ValueError:
                continue
    return result


def _parse_network(value: str) -> dict[str, int]:
    received = transmitted = 0
    for line in value.splitlines()[2:]:
        _, separator, tail = line.partition(":")
        fields = tail.split() if separator else []
        if len(fields) >= 9:
            try:
                received += int(fields[0])
                transmitted += int(fields[8])
            except ValueError:
                continue
    return {"network_rx_bytes": received, "network_tx_bytes": transmitted}


def _parse_processes(value: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for line in value.splitlines()[:20]:
        fields = line.split(None, 3)
        if len(fields) == 4:
            try:
                items.append({"pid": fields[0], "name": fields[1][:120], "cpu_percent": float(fields[2]), "memory_percent": float(fields[3])})
            except ValueError:
                continue
    return items


def _parse_services(value: str) -> list[dict[str, str]]:
    return [
        {"name": fields[0][:180], "state": fields[3][:30]}
        for line in value.splitlines()[:100]
        if len(fields := line.split(None, 4)) >= 4
    ]


def _parse_containers(value: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in value.splitlines()[:100]:
        fields = line.split("\t", 2)
        if len(fields) == 3:
            items.append({"name": fields[0][:120], "image": fields[1][:180], "status": fields[2][:180]})
    return items
