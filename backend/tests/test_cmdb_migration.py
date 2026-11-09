import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_cmdb_migration_is_additive_and_has_expected_constraints(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "cmdb-migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        relation_indexes = connection.execute(
            "PRAGMA index_list('cmdb_service_assets')"
        ).fetchall()

    assert {
        "assets",
        "cmdb_businesses",
        "cmdb_applications",
        "cmdb_services",
        "cmdb_service_apis",
        "cmdb_service_assets",
        "cmdb_change_audits",
        "http_probes",
        "http_probe_runs",
    } <= tables
    assert any(index[2] == 1 for index in relation_indexes)
