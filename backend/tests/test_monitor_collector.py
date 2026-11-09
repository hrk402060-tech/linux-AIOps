from src.services.monitor_collector import (
    _cpu_percent, _parse_containers, _parse_disks, _parse_meminfo, _parse_network,
    _parse_processes, _parse_services,
)


class FakeStream:
    def __init__(self, value: str) -> None:
        self.value = value.encode()
        self.channel = self

    def read(self, _size: int) -> bytes:
        return self.value

    def recv_exit_status(self) -> int:
        return 0


class FakeClient:
    def exec_command(self, _command: str, timeout: int) -> tuple[None, FakeStream, FakeStream]:
        assert timeout == 30
        samples = "cpu 100 0 100 800 0 0 0 0\ncpu 150 0 150 900 0 0 0 0\n"
        return None, FakeStream(samples), FakeStream("")


def test_parses_bounded_linux_metrics() -> None:
    assert _cpu_percent(FakeClient()) == 50.0  # type: ignore[arg-type]
    memory = _parse_meminfo(
        "MemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 100 kB\nSwapFree: 80 kB"
    )
    assert memory["memory_percent"] == 75.0
    assert memory["swap_percent"] == 20.0
    disks = _parse_disks(
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda 100 80 20 80% /",
        "Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/vda 100 40 60 40% /",
    )
    assert disks == [{"mount": "/", "used_percent": 80.0, "inode_percent": 40.0, "inode_observed": True}]
    assert _parse_disks("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda 100 80 20 80% /", "")[0]['inode_observed'] is False
    assert _parse_network("Inter-| x\n face | x\n eth0: 10 0 0 0 0 0 0 0 20") == {
        "network_rx_bytes": 10, "network_tx_bytes": 20,
    }
    assert _parse_processes("1 systemd 0.1 0.2")[0]["name"] == "systemd"
    assert _parse_services("sshd.service loaded active running SSH")[0]["state"] == "running"
    assert _parse_containers("nginx\tnginx:1.27\tUp 1 hour")[0]["image"] == "nginx:1.27"
