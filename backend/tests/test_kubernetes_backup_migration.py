import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_backup_migration_roundtrip_preserves_previous_schema(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    env = {**os.environ, "DATABASE_URL": url}

    def run(*arguments: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=root, env=env, capture_output=True, text=True, check=True, timeout=60,
        )

    run("upgrade", "20261119_56")
    engine = create_engine(url)
    before = set(inspect(engine).get_table_names())
    run("upgrade", "20261120_57")
    assert "kubernetes_backup_artifacts" in set(inspect(engine).get_table_names())
    run("downgrade", "20261119_56")
    assert set(inspect(engine).get_table_names()) == before
    engine.dispose()
