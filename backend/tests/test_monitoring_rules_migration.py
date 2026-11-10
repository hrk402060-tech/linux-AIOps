import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def _migrate(path: Path, command: str, revision: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{path}"},
        check=True, capture_output=True, text=True, timeout=60,
    )


def _seed_legacy(path: Path) -> None:
    when = "2026-11-10 12:30:00"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO assets "
            "(id, name, host, ssh_port, os_family, environment, group_name, "
            "labels, status, lifecycle_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "legacy", "10.0.0.51", 22, "ubuntu", "test", "legacy",
             "[]", "online", "enabled", when, when),
        )
        connection.execute(
            "INSERT INTO monitoring_policies "
            "(asset_id, cpu_threshold, memory_threshold, swap_threshold, "
            "disk_threshold, inode_threshold, load_threshold, duration_minutes, "
            "recovery_notify, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", 81, 82, 78, 83, 84, 7, 9, 0, when),
        )
        connection.execute(
            "INSERT INTO monitoring_snapshots "
            "(id, asset_id, status, read_only, metrics, collected_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-sample", "legacy", "online", 1, '{"cpu_percent": 10}', when),
        )


def _legacy_policy(path: Path) -> tuple[object, ...]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT cpu_threshold, memory_threshold, swap_threshold, "
            "disk_threshold, inode_threshold, load_threshold, duration_minutes, "
            "recovery_notify, updated_at FROM monitoring_policies WHERE asset_id = ?",
            ("legacy",),
        ).fetchone()


def test_severity_migration_keeps_legacy_policy_and_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "monitoring-rules.db"
    _migrate(path, "upgrade", "20261107_50")
    _seed_legacy(path)
    original = _legacy_policy(path)
    _migrate(path, "upgrade", "20261110_51")
    assert _legacy_policy(path) == original
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT severity_rules FROM monitoring_policies WHERE asset_id = ?",
            ("legacy",),
        ).fetchone() == (None,)
        connection.execute(
            "UPDATE monitoring_policies SET severity_rules = ? WHERE asset_id = ?",
            (json.dumps({"duration_minutes": 0}), "legacy"),
        )
    _migrate(path, "downgrade", "20261107_50")
    assert _legacy_policy(path) == original
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(monitoring_policies)",
        )}
        assert "severity_rules" not in columns
        assert connection.execute(
            "SELECT metrics FROM monitoring_snapshots WHERE id = ?",
            ("legacy-sample",),
        ).fetchone() == ('{"cpu_percent": 10}',)
