import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_notification_migration_upgrade_downgrade(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    path = tmp_path / 'notification-migration.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{path}'}
    for command in ('upgrade', 'downgrade'):
        revision = 'head' if command == 'upgrade' else '20260925_27'
        subprocess.run([sys.executable, '-m', 'alembic', command, revision], cwd=backend_root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)
        with sqlite3.connect(path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {'alert_notification_config', 'alert_notifications', 'alert_notification_receipts', 'alert_notification_audits'}
        assert expected <= tables if command == 'upgrade' else not expected.intersection(tables)
