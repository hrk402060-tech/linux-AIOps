import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_recovery_migration_preserves_existing_notifications(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'recovery-migration.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database}'}

    def migrate(action: str, revision: str) -> None:
        subprocess.run([sys.executable, '-m', 'alembic', action, revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)

    migrate('upgrade', '20261009_31')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO alert_notifications(id,dedupe_key,kind,status,subject,body,asset_locator,recipients,attempts,max_attempts,created_at) VALUES ('old-notice','old-notice','diagnostic_result','blocked','immutable','unchanged','{}','[]',0,5,'2026-10-09')")
        legacy = connection.execute('SELECT * FROM alert_notifications').fetchone()
    migrate('upgrade', '20261010_32')
    with sqlite3.connect(database) as connection:
        row = connection.execute('SELECT * FROM alert_notifications').fetchone()
        assert row[:-1] == legacy and row[-1] is None
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'recovery_verifications', 'recovery_evaluations'} <= tables
        assert all(row[6] == 'RESTRICT' for row in connection.execute('PRAGMA foreign_key_list(recovery_verifications)'))
        assert 'recovery_evaluations' in {row[2] for row in connection.execute('PRAGMA foreign_key_list(alert_notifications)')}
    migrate('downgrade', '20261009_31')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM alert_notifications').fetchone() == legacy
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'recovery_verifications' not in tables and 'diagnostic_jobs' in tables
