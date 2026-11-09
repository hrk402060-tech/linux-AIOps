import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_incident_migration_creates_projection_and_timeline(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "incidents-migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_root, env=environment, check=True, capture_output=True,
        text=True, timeout=60,
    )
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('incidents')"
        ).fetchall()
    assert {"incidents", "incident_timeline"} <= tables
    assert any(row[2] == "assets" and row[6] == "RESTRICT" for row in foreign_keys)
