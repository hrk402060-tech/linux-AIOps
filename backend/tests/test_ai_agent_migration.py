import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_agent_reviewer_migration_preserves_legacy_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "agent-migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database}"}

    def migrate(command: str, revision: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", command, revision], cwd=root,
                       env=environment, check=True, capture_output=True, text=True, timeout=60)

    migrate("upgrade", "20261003_28")
    with sqlite3.connect(database) as connection:
        connection.execute("""INSERT INTO agent_recommendations
            (id, finding_id, cve_id, asset_name, provider_id, provider_name, status, priority,
             risk_analysis, remediation_steps, validation_steps, rollback_plan, evidence_snapshot,
             created_at, reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacy-review", "demo-finding", "CVE-2099-1", "demo", "local-rules", "demo",
             "approved", "high", "legacy evidence", "[]", "[]", "demo", "[]",
             "2026-10-01 00:00:00", "2026-10-02 00:00:00"))
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        status, evidence, timestamp, reviewer = connection.execute(
            "SELECT status,risk_analysis,reviewed_at,reviewed_by FROM agent_recommendations"
        ).fetchone()
        assert (status, evidence, timestamp, reviewer) == (
            "approved", "legacy evidence", "2026-10-02 00:00:00", None,
        )
        connection.execute("UPDATE agent_recommendations SET reviewed_by=?", ("demo-user",))
    migrate("downgrade", "20261003_28")
    with sqlite3.connect(database) as connection:
        assert "reviewed_by" not in {row[1] for row in connection.execute(
            "PRAGMA table_info(agent_recommendations)"
        )}
        assert connection.execute("SELECT risk_analysis FROM agent_recommendations").fetchone()[0] == "legacy evidence"
