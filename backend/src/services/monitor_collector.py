from __future__ import annotations

from math import isfinite
import re
import time
from typing import TYPE_CHECKING

from src.models.asset import Asset
from src.models.credential import Credential
from src.services.scan_engine import ScanCollectionError, _connect, _execute

OPTIONAL_OUTPUT_BYTES = 64 * 1024
MAX_NETWORK_SAMPLE_SECONDS = 30

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
            _execute(client, "LC_ALL=C df -Pk -x tmpfs -x devtmpfs"),
            _execute(client, "LC_ALL=C df -Pi -x tmpfs -x devtmpfs"),
        )
        return {
            "cpu_percent": _cpu_percent(client),
            "cpu_count": _parse_cpu_count(_execute_optional(
                client, "getconf _NPROCESSORS_ONLN",
            )),
            "load_1m": load[0],
            "load_5m": load[1],
            "load_15m": load[2],
            **memory,
            "disk_percent": max((item["used_percent"] for item in disks), default=0.0),
            "disk_observed": bool(disks),
            "inode_percent": max((item["inode_percent"] for item in disks), default=0.0),
            "inode_observed": any(item["inode_observed"] for item in disks),
            "disks": disks,
            **_network_metrics(client),
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
    if any(not isfinite(value) or value < 0 for sample in samples for value in sample):
        raise ScanCollectionError("CPU 指标采样无效")
    size = min(map(len, samples))
    first, second = samples[0][:size], samples[1][:size]
    total = sum(second) - sum(first)
    idle = (second[3] + second[4]) - (first[3] + first[4])
    if not isfinite(total) or total <= 0 or not isfinite(idle) or not 0 <= idle <= total:
        raise ScanCollectionError("CPU 指标采样无效")
    return round((total - idle) * 100 / total, 1)


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
    inode_map = _df_rows(inodes)
    items: list[dict[str, object]] = []
    for mount, disk in _df_rows(space).items():
        inode = inode_map.get(mount)
        if inode and (inode["total"] <= 0 or inode["used"] + inode["available"] > inode["total"]):
            inode = None
        items.append({
            "device": disk["device"], "mount": mount,
            "total_bytes": int(disk["total"]) * 1024,
            "used_bytes": int(disk["used"]) * 1024,
            "available_bytes": int(disk["available"]) * 1024,
            "used_percent": disk["percent"],
            "inode_total": inode["total"] if inode else None,
            "inode_used": inode["used"] if inode else None,
            "inode_available": inode["available"] if inode else None,
            "inode_percent": inode["percent"] if inode else 0.0,
            "inode_observed": inode is not None,
        })
    return items


def _df_rows(value: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for line in value.splitlines()[1:]:
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[4].endswith("%"):
            continue
        try:
            total, used, available = (int(item) for item in fields[1:4])
            percent = float(fields[4][:-1])
        except ValueError:
            continue
        if min(total, used, available, percent) < 0 or not isfinite(percent):
            continue
        result[_unescape_mount(fields[5])] = {
            "device": _unescape_mount(fields[0]), "total": total, "used": used,
            "available": available, "percent": percent,
        }
    return result


def _unescape_mount(value: str) -> str:
    return re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)


def _parse_network(value: str) -> dict[str, int]:
    interfaces = _parse_network_interfaces(value)
    return {
        "network_rx_bytes": sum(item["rx_bytes"] for item in interfaces),
        "network_tx_bytes": sum(item["tx_bytes"] for item in interfaces),
    }


def _parse_network_interfaces(value: str) -> list[dict[str, object]]:
    interfaces: list[dict[str, object]] = []
    for line in value.splitlines()[2:]:
        name, separator, tail = line.partition(":")
        fields = tail.split() if separator else []
        if len(fields) >= 9:
            try:
                received, transmitted = int(fields[0]), int(fields[8])
            except ValueError:
                continue
            if name.strip() and min(received, transmitted) >= 0:
                interfaces.append({
                    "name": name.strip(), "rx_bytes": received, "tx_bytes": transmitted,
                })
    return interfaces


def _parse_cpu_count(value: str) -> int | None:
    try:
        count = int(value.strip())
    except ValueError:
        return None
    return count if count > 0 else None


def _network_metrics(client: paramiko.SSHClient) -> dict[str, object]:
    command = "cat /proc/sys/kernel/random/boot_id /proc/uptime /proc/net/dev"
    first = _network_sample(_execute(client, command))
    time.sleep(0.2)
    second = _network_sample(_execute(client, command))
    first_boot, first_time, previous = first
    second_boot, second_time, interfaces = second
    elapsed = second_time - first_time if first_time is not None and second_time is not None else 0
    previous_by_name = {item["name"]: item for item in previous}
    for interface in interfaces:
        prior = previous_by_name.get(interface["name"])
        valid = bool(
            first_boot and first_boot == second_boot and prior
            and isfinite(elapsed) and 0 < elapsed <= MAX_NETWORK_SAMPLE_SECONDS
        )
        rx_delta = int(interface["rx_bytes"]) - int(prior["rx_bytes"]) if prior else -1
        tx_delta = int(interface["tx_bytes"]) - int(prior["tx_bytes"]) if prior else -1
        valid = valid and rx_delta >= 0 and tx_delta >= 0
        interface["rx_bytes_per_second"] = round(rx_delta / elapsed, 2) if valid else None
        interface["tx_bytes_per_second"] = round(tx_delta / elapsed, 2) if valid else None
    return {
        "network_rx_bytes": sum(int(item["rx_bytes"]) for item in interfaces),
        "network_tx_bytes": sum(int(item["tx_bytes"]) for item in interfaces),
        "network_interfaces": interfaces,
    }


def _network_sample(value: str) -> tuple[str, float | None, list[dict[str, object]]]:
    lines = value.splitlines()
    if len(lines) < 4:
        return "", None, []
    try:
        uptime = float(lines[1].split()[0])
    except (ValueError, IndexError):
        uptime = None
    if uptime is not None and (not isfinite(uptime) or uptime < 0):
        uptime = None
    return lines[0].strip(), uptime, _parse_network_interfaces("\n".join(lines[2:]))


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
