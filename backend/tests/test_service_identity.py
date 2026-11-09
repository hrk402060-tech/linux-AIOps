from types import SimpleNamespace
from unittest.mock import Mock

from src.services import service_identity


def test_listen_port_parses_ipv4_ipv6_and_rejects_invalid_values() -> None:
    assert service_identity.listen_port("0.0.0.0:22") == 22
    assert service_identity.listen_port("[::]:8443") == 8443
    assert service_identity.listen_port("*:8080") == 8080
    assert service_identity.listen_port("unix:/run/service.sock") is None
    assert service_identity.listen_port("0.0.0.0:0") is None


def test_probe_banner_uses_loopback_timeout_and_bounded_output() -> None:
    stdout = SimpleNamespace(
        read=lambda size: b"SSH-2.0-OpenSSH_9.3\r\n",
        channel=SimpleNamespace(recv_exit_status=lambda: 0),
    )
    client = SimpleNamespace(exec_command=Mock(return_value=(None, stdout, None)))

    banner = service_identity.probe_banner(client, "tcp", "0.0.0.0:22")

    assert banner == "SSH-2.0-OpenSSH_9.3"
    command = client.exec_command.call_args.args[0]
    assert "127.0.0.1/22" in command
    assert "timeout 3" in command


def test_probe_banner_returns_empty_for_udp_or_unreadable_service() -> None:
    client = SimpleNamespace(
        exec_command=lambda command, timeout: (_ for _ in ()).throw(OSError("blocked"))
    )

    assert service_identity.probe_banner(client, "udp", "0.0.0.0:53") == ""
    assert service_identity.probe_banner(client, "tcp", "0.0.0.0:53") == ""
