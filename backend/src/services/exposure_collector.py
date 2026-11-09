import ipaddress

import paramiko

from src.models.asset import Asset
from src.models.credential import Credential
from src.services import scan_engine

METHODOLOGY_VERSION = "2026.09"
COMMAND = "sh -c 'command -v ss >/dev/null && ss -H -lntup 2>/dev/null || true'"


def collect(
    asset: Asset, credential: Credential, secret: str,
    expected_host_key: paramiko.PKey | None = None,
) -> list[dict[str, object]]:
    client = scan_engine._connect(asset, credential, secret, expected_host_key)
    try:
        output = scan_engine._execute(client, COMMAND)
    finally:
        client.close()
    return parse_listeners(output)


def parse_listeners(output: str) -> list[dict[str, object]]:
    listeners: list[dict[str, object]] = []
    for line in output.splitlines()[:500]:
        fields = line.split()
        if len(fields) < 5:
            continue
        host, port = _split_endpoint(fields[4])
        scope, conclusion = _scope(host)
        listeners.append({
            "protocol": fields[0], "listen_host": host, "port": port,
            "process": " ".join(fields[5:])[:300], "scope": scope,
            "conclusion": conclusion, "evidence_source": "SSH 只读 ss 监听快照",
        })
    return listeners


def _split_endpoint(endpoint: str) -> tuple[str, int | None]:
    host, separator, port = endpoint.rpartition(":")
    return (host.strip("[]") or "*", int(port)) if separator and port.isdigit() else (endpoint, None)


def _scope(host: str) -> tuple[str, str]:
    if host in {"*", "0.0.0.0", "::"}:
        return "all_interfaces", "潜在暴露，需结合防火墙与外部探测确认"
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return "specific_interface", "绑定指定接口，需人工确认网络可达性"
    if address.is_loopback:
        return "loopback", "仅本机监听"
    return "specific_interface", "绑定指定接口，需人工确认网络可达性"
