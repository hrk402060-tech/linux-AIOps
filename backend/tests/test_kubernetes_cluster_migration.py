import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_cluster_migration_upgrade_downgrade_preserves_existing_tables(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    env = {**os.environ, "DATABASE_URL": url}
    def run(*arguments: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments], cwd=root, env=env,
            capture_output=True, text=True, check=True, timeout=60,
        )
    run("upgrade", "20261110_51")
    engine = create_engine(url)
    before = set(inspect(engine).get_table_names())
    run("upgrade", "head")
    added = {"kubernetes_clusters", "kubernetes_cluster_credentials",
             "kubernetes_cluster_audits", "kubernetes_connection_tests", "kubernetes_collection_runs"}
    assert set(inspect(engine).get_table_names()) == before | added
    keys = inspect(engine).get_foreign_keys("kubernetes_clusters")
    assert any(item["referred_table"] == "cmdb_services" for item in keys)
    run("downgrade", "20261110_51")
    assert set(inspect(engine).get_table_names()) == before
    run("upgrade", "head")
    assert set(inspect(engine).get_table_names()) == before | added
    engine.dispose()
