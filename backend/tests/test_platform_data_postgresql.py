from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.database import Base
from src.models.platform_data import PlatformDataJob
from src.services.platform_data_snapshot import capture, verify_restore
from src.services.platform_data_worker import _claim

pytest_plugins = ("test_kubernetes_cluster_postgresql",)


def test_postgresql_data_job_is_claimed_once(isolated_pg: tuple[Engine, str]) -> None:
    engine, _ = isolated_pg
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    identifier = str(uuid4())
    with factory() as session:
        session.add(PlatformDataJob(
            id=identifier, kind="cleanup", status="queued", active_key="storage",
            operator_username="system", parameters={}, result={},
        ))
        session.commit()
    barrier = Barrier(2)

    def claim() -> tuple[str, str] | None:
        with factory() as session:
            barrier.wait(timeout=10)
            return _claim(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=20) for future in results]
    assert len([claim for claim in claims if claim is not None]) == 1
    with factory() as session:
        assert session.get(PlatformDataJob, identifier).status == "running"


def test_postgresql_expired_claim_is_not_replayed(isolated_pg: tuple[Engine, str]) -> None:
    engine, _ = isolated_pg
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(PlatformDataJob(
            id=str(uuid4()), kind="cleanup", status="running", active_key="storage",
            operator_username="system", parameters={}, result={}, lease_token=str(uuid4()),
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        session.commit()
    with factory() as session:
        assert _claim(session) is None
    with factory() as session:
        item = session.scalar(select(PlatformDataJob))
        assert item.status == "failed" and item.error_code == "worker_interrupted"


def test_postgresql_logical_snapshot_restores_real_rows(
    isolated_pg: tuple[Engine, str],
) -> None:
    from src.config import Settings

    engine, _ = isolated_pg
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)
    with factory() as session:
        session.add(PlatformDataJob(
            id=str(uuid4()), kind="backup", status="completed",
            operator_username="system", parameters={"retention_days": 365},
            result={"verified": True},
        ))
        session.commit()
    snapshot = capture(engine, Settings())
    result = verify_restore(snapshot)
    assert result["verified"] is True and result["row_count"] >= 1
    assert result["verification_target"] == "isolated_sqlite"


def test_postgresql_data_job_migration_roundtrip_preserves_existing_records(
    isolated_pg: tuple[Engine, str],
) -> None:
    engine, url = isolated_pg
    root = Path(__file__).resolve().parents[1]
    parsed = make_url(url)
    env = {
        **os.environ,
        "DATABASE_URL": parsed.difference_update_query(["options"]).render_as_string(hide_password=False),
        "PGOPTIONS": parsed.query["options"],
    }

    def migrate(*arguments: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments], cwd=root, env=env,
            capture_output=True, text=True, check=False, timeout=90,
        )
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "20261117_54")
    before = set(inspect(engine).get_table_names())
    metadata = MetaData()
    settings = Table("platform_settings", metadata, autoload_with=engine)
    assets = Table("assets", metadata, autoload_with=engine)
    snapshots = Table("monitoring_snapshots", metadata, autoload_with=engine)
    marker, asset_id, snapshot_id = f"migration-{uuid4()}", str(uuid4()), str(uuid4())
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(settings.insert(), {
            "key": marker, "value": "preserve-existing-settings", "updated_by": "admin", "updated_at": now,
        })
        connection.execute(assets.insert(), {
            "id": asset_id, "name": marker, "host": "192.0.2.70", "ssh_port": 22,
            "os_family": "ubuntu", "environment": "test", "group_name": "migration",
            "labels": [], "status": "online", "lifecycle_status": "enabled",
            "created_at": now, "updated_at": now,
        })
        connection.execute(snapshots.insert(), {
            "id": snapshot_id, "asset_id": asset_id, "status": "online", "read_only": True,
            "metrics": {"cpu_percent": 17.5}, "collected_at": now,
        })

    def existing_rows() -> tuple[dict, dict, dict]:
        with engine.connect() as connection:
            return (
                dict(connection.execute(select(settings).where(settings.c.key == marker)).mappings().one()),
                dict(connection.execute(select(assets).where(assets.c.id == asset_id)).mappings().one()),
                dict(connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id)).mappings().one()),
            )

    preserved = existing_rows()
    migrate("upgrade", "20261117_55")
    assert set(inspect(engine).get_table_names()) == before | {"platform_data_jobs"}
    assert existing_rows() == preserved
    inspector = inspect(engine)
    indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes("platform_data_jobs")}
    assert indexes["ix_platform_data_jobs_status"] == ["status"]
    assert indexes["ix_platform_data_jobs_created_at"] == ["created_at"]
    assert any(item["name"] == "uq_platform_data_active" and item["column_names"] == ["active_key"]
               for item in inspector.get_unique_constraints("platform_data_jobs"))
    jobs = Table("platform_data_jobs", MetaData(), autoload_with=engine)

    def row(active_key: str | None) -> dict:
        return {
            "id": str(uuid4()), "kind": "cleanup", "status": "queued" if active_key else "completed",
            "active_key": active_key, "operator_username": "admin", "parameters": {},
            "result": {}, "created_at": now,
        }

    with engine.begin() as connection:
        connection.execute(jobs.insert(), [row("storage"), row(None), row(None)])
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(jobs.insert(), row("storage"))
    migrate("downgrade", "20261117_54")
    assert set(inspect(engine).get_table_names()) == before
    assert existing_rows() == preserved
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "20261117_54"
