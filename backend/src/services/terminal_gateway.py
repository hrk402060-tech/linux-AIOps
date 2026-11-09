from __future__ import annotations

from typing import Protocol

import paramiko

from src.models.asset import Asset
from src.models.credential import Credential, CredentialType
from src.services.ssh_connection import open_client


class InteractiveShell(Protocol):
    def receive(self) -> bytes: ...
    def has_output(self) -> bool: ...
    def is_active(self) -> bool: ...
    def send(self, value: str) -> None: ...
    def resize(self, columns: int, rows: int) -> None: ...
    def close(self) -> None: ...


class ParamikoShell:
    def __init__(self, client: paramiko.SSHClient, channel: paramiko.Channel) -> None:
        self._client = client
        self._channel = channel

    def receive(self) -> bytes:
        return self._channel.recv(65536)

    def has_output(self) -> bool:
        return self._channel.recv_ready()

    def is_active(self) -> bool:
        return not self._channel.closed and not self._channel.exit_status_ready()

    def send(self, value: str) -> None:
        self._channel.sendall(value.encode("utf-8"))

    def resize(self, columns: int, rows: int) -> None:
        self._channel.resize_pty(width=columns, height=rows)

    def close(self) -> None:
        try:
            self._channel.close()
        finally:
            self._client.close()


def open_shell(
    asset: Asset, credential: Credential, secret: str, columns: int, rows: int,
    expected_host_key: paramiko.PKey | None = None,
) -> InteractiveShell:
    del expected_host_key
    client = open_client(asset, credential, secret)
    try:
        channel = client.invoke_shell(
            term="xterm-256color", width=columns, height=rows
        )
        channel.settimeout(1.0)
        return ParamikoShell(client, channel)
    except (OSError, paramiko.SSHException, ValueError):
        client.close()
        raise
