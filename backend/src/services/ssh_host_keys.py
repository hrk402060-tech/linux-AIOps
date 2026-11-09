import base64
import binascii
import hashlib
import socket
from dataclasses import dataclass

import paramiko

from src.models.ssh_host_key import SshHostKey


@dataclass(frozen=True)
class HostKeyCandidate:
    key_type: str
    fingerprint: str
    public_key: str


def probe_host_key(host: str, port: int) -> HostKeyCandidate:
    transport: paramiko.Transport | None = None
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=10)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        public_key = f"{key.get_name()} {key.get_base64()}"
        return HostKeyCandidate(key.get_name(), _fingerprint(key.asbytes()), public_key)
    finally:
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()


def verify_candidate(host: str, port: int, fingerprint: str) -> HostKeyCandidate:
    candidate = probe_host_key(host, port)
    if candidate.fingerprint != fingerprint:
        raise ValueError("主机指纹已变化，请重新获取并人工确认")
    return candidate


def host_key_from_record(record: SshHostKey) -> paramiko.PKey:
    parts = record.public_key.split(" ", 2)
    if len(parts) < 2 or parts[0] != record.key_type:
        raise ValueError("已登记主机密钥格式无效")
    try:
        key = paramiko.PKey.from_type_string(
            parts[0], base64.b64decode(parts[1], validate=True)
        )
    except (binascii.Error, paramiko.SSHException, ValueError) as exc:
        raise ValueError("已登记主机密钥格式无效") from exc
    if _fingerprint(key.asbytes()) != record.fingerprint:
        raise ValueError("已登记主机密钥指纹不一致")
    return key


def _fingerprint(value: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(value).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"
