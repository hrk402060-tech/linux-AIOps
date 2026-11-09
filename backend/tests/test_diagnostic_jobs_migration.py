import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_queue_migration_preserves_legacy_analysis_and_seeds_global_slots(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / 'queue-migration.db'
    environment = {**os.environ, 'DATABASE_URL': f'sqlite:///{database}'}
    def migrate(action: str, revision: str) -> None:
        subprocess.run([sys.executable, '-m', 'alembic', action, revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)
    migrate('upgrade', '20261007_30')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO root_cause_analyses(id,alert_id,asset_id,status,trigger_type,policy_version,summary,evidence,operator,trace_id,started_at,finished_at) VALUES ('legacy','legacy-alert','legacy-asset','completed','automatic','readonly-linux-2026.10.07','immutable','{\"uptime\":10}','system','trace','2026-10-07','2026-10-07')")
        legacy = connection.execute('SELECT * FROM root_cause_analyses').fetchone()
        connection.execute("INSERT INTO alert_notifications(id,dedupe_key,alert_id,analysis_id,kind,status,subject,body,asset_locator,recipients,attempts,max_attempts,created_at) VALUES ('legacy-outbox','diagnostic:legacy','legacy-alert','legacy','diagnostic_result','blocked','legacy','immutable notice','{}','[]',0,5,'2026-10-07')")
        notice = connection.execute('SELECT * FROM alert_notifications').fetchone()
    migrate('upgrade', '20261009_31')
    with sqlite3.connect(database) as connection:
        row = connection.execute('SELECT * FROM root_cause_analyses').fetchone()
        assert row[:-2] == legacy and row[-2:] == (None, None)
        assert connection.execute('SELECT * FROM alert_notifications').fetchone() == notice
        assert connection.execute('SELECT a.evidence FROM alert_notifications n JOIN root_cause_analyses a ON a.id=n.analysis_id').fetchone()[0] == '{"uptime":10}'
        assert connection.execute('SELECT id,revision FROM diagnostic_admission').fetchall() == [(1, 0)]
        assert connection.execute('SELECT id,job_id FROM diagnostic_slots ORDER BY id').fetchall() == [(1, None), (2, None)]
        assert all(row[6] == 'RESTRICT' for row in connection.execute('PRAGMA foreign_key_list(diagnostic_jobs)'))
        assert 'diagnostic_jobs' in {row[2] for row in connection.execute('PRAGMA foreign_key_list(root_cause_analyses)')}
    migrate('downgrade', '20261007_30')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT * FROM root_cause_analyses').fetchone() == legacy
        assert connection.execute('SELECT * FROM alert_notifications').fetchone() == notice
        assert 'diagnostic_jobs' not in {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
