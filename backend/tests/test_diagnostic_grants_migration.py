import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_diagnostic_migration_is_additive_and_preserves_existing_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'diagnostic-migration.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database}'}
    def migrate(command: str, revision: str) -> None:
        subprocess.run([sys.executable, '-m', 'alembic', command, revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)
    migrate('upgrade', '20261004_29')
    with sqlite3.connect(database) as connection:
        before = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.execute("INSERT INTO alembic_version (version_num) SELECT 'sentinel' WHERE 0")
        connection.execute("CREATE TABLE diagnostic_legacy_marker(id TEXT PRIMARY KEY, evidence TEXT)")
        connection.execute("INSERT INTO diagnostic_legacy_marker VALUES ('legacy','immutable evidence')")
    migrate('upgrade', 'head')
    with sqlite3.connect(database) as connection:
        after = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert before <= after
        assert {'diagnostic_grants', 'diagnostic_grant_audits'} <= after
        assert connection.execute('SELECT evidence FROM diagnostic_legacy_marker').fetchone()[0] == 'immutable evidence'
        assert connection.execute('SELECT COUNT(*) FROM diagnostic_grants').fetchone()[0] == 0
    migrate('downgrade', '20261004_29')
    with sqlite3.connect(database) as connection:
        remaining = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'diagnostic_grants' not in remaining and 'diagnostic_grant_audits' not in remaining
        assert before <= remaining
        assert connection.execute('SELECT evidence FROM diagnostic_legacy_marker').fetchone()[0] == 'immutable evidence'
