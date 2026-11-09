import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_removal_migration_preserves_scan_and_fk(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'scan-removal-migration.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database}'}

    def migrate(action: str, revision: str) -> None:
        subprocess.run([sys.executable, '-m', 'alembic', action, revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)

    migrate('upgrade', '20261007_30')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO scan_tasks(id,name,scan_type,status,progress,read_only,asset_ids,asset_snapshot,findings,execution_log,archived,report_content,created_at,updated_at) VALUES ('legacy-task','DEMO retained','comprehensive','completed',100,1,'[]','[]','[]','[]',0,'immutable report','2026-10-12','2026-10-12')")
        legacy = connection.execute('SELECT * FROM scan_tasks').fetchone()
    migrate('upgrade', '20261012_31')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM scan_tasks').fetchone() == legacy
        keys = connection.execute('PRAGMA foreign_key_list(scan_task_removals)').fetchall()
        assert len(keys) == 1 and keys[0][2] == 'scan_tasks' and keys[0][6] == 'RESTRICT'
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute("INSERT INTO scan_task_removals VALUES ('removal','legacy-task','demo-admin','demo-user','No longer needed','{}','2026-10-12')")
        try:
            connection.execute("DELETE FROM scan_tasks WHERE id='legacy-task'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('Deletion evidence FK must restrict physical task deletion')
    migrate('downgrade', '20261007_30')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM scan_tasks').fetchone() == legacy
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='scan_task_removals'").fetchall()
