import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_workspace_merge_retains_independent_removal_branch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'workspace-removal-merge.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database}'}

    def migrate(revision: str) -> None:
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)

    migrate('20261012_31')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO scan_tasks(id,name,scan_type,status,progress,read_only,asset_ids,asset_snapshot,findings,execution_log,archived,report_content,created_at,updated_at) VALUES ('merge-task','DEMO retained','comprehensive','completed',100,1,'[]','[]','[]','[]',0,'immutable report','2026-10-12','2026-10-12')")
        connection.execute("INSERT INTO scan_task_removals VALUES ('merge-removal','merge-task','demo-admin','demo-user','No longer needed','{}','2026-10-12')")
        before = connection.execute('SELECT * FROM scan_tasks').fetchall()
        audit = connection.execute('SELECT * FROM scan_task_removals').fetchall()
    migrate('20261012_33')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM scan_tasks').fetchall() == before
        assert connection.execute('SELECT * FROM scan_task_removals').fetchall() == audit
        assert connection.execute('SELECT version_num FROM alembic_version').fetchall() == [('20261012_33',)]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'diagnostic_jobs', 'recovery_verifications', 'recovery_evaluations', 'scan_task_removals'} <= tables
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
