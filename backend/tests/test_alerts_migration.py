import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_alert_migration_creates_evidence_tables(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "alerts-migration.db"
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
        indexes = connection.execute("PRAGMA index_list('alerts')").fetchall()
    assert {
        "alert_connectors", "alerts", "alert_timeline", "root_cause_analyses",
        "alert_connector_audits",
    } <= tables
    assert any(index[2] == 1 for index in indexes)
