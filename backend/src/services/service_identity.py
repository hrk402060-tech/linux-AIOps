import shlex
from typing import Protocol

import paramiko


MAX_BANNER_BYTES = 512
MAX_BANNER_TEXT = 256
MAX_BANNER_PROBES = 16


class CommandClient(Protocol):
    def exec_command(self, command: str, timeout: float) -> tuple[object, object, object]:
        ...


def listen_port(value: str) -> int | None:
    candidate = value.rsplit(":", 1)[-1].strip().strip("]")
    if not candidate.isdigit():
        return None
    port = int(candidate)
    return port if 1 <= port <= 65535 else None


def probe_banner(client: CommandClient, protocol: str, listen: str) -> str:
    if protocol.lower() not in {"tcp", "tcp6"}:
        return ""
    port = listen_port(listen)
    if port is None:
        return ""
    command = _probe_command(port)
    try:
        _, stdout, _ = client.exec_command(command, timeout=4)
        raw = stdout.read(MAX_BANNER_BYTES + 1)
        if len(raw) > MAX_BANNER_BYTES or stdout.channel.recv_exit_status() != 0:
            return ""
    except (AttributeError, OSError, ValueError, EOFError, paramiko.SSHException):
        return ""
    return _sanitize(raw.decode("utf-8", errors="replace"))


def _probe_command(port: int) -> str:
    script = (
        f"exec 3<>/dev/tcp/127.0.0.1/{port} 2>/dev/null || exit 0; "
        f"timeout 2 dd if=/dev/fd/3 bs=1 count={MAX_BANNER_BYTES} 2>/dev/null"
    )
    return (
        "command -v bash >/dev/null 2>&1 && "
        "command -v timeout >/dev/null 2>&1 && "
        f"timeout 3 bash -c {shlex.quote(script)} || true"
    )


def _sanitize(value: str) -> str:
    printable = "".join(char for char in value if char.isprintable() or char in "\r\n\t")
    return " ".join(printable.split())[:MAX_BANNER_TEXT]
