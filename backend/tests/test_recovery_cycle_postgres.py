from __future__ import annotations

import base64
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib
import os
import pkgutil
from queue import Queue
from threading import Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from src import models
from src.database import Base
from src.models.alert import Alert, AlertConnector
from src.models.alert_notification import AlertNotification
from src.models.asset import Asset, AssetEnvironment, OsFamily
from src.models.diagnostic_job import DiagnosticAdmission
from src.models.monitoring import MonitoringSnapshot
from src.models.recovery import RecoveryEvaluation, RecoveryVerification
from src.schemas.alert import AlertIngest
from src.schemas.recovery import RecoveryAction, RecoveryCreate
from src.services import alerts, recovery
from src.services.alert_security import AlertConflictError
from src.services.crypto import CredentialVault

TEST_DATABASE_URL = os.environ.get("RECOVERY_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Requires an isolated PostgreSQL Unix-socket test database",
)


@dataclass(frozen=True)
class CycleDatabase:
    engine: Engine
    factory: sessionmaker[Session]
    alert_id: str
    asset_id: str
    connector_id: str
    verification_id: str
    vault: CredentialVault


def _seed(engine: Engine) -> CycleDatabase:
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    asset_id, connector_id, alert_id = (str(uuid4()) for _ in range(3))
    with factory() as session:
        session.add_all([
            Asset(id=asset_id, name="isolated-cycle-node", host="192.0.2.42",
                  os_family=OsFamily.OPENEULER, environment=AssetEnvironment.TEST),
            AlertConnector(id=connector_id, name="isolated-cycle-source", source_type="generic",
                           encrypted_secret=os.urandom(32), nonce=os.urandom(12),
                           key_version="synthetic", secret_hint="unused", operator="test"),
            DiagnosticAdmission(id=1, revision=0),
        ])
        session.flush()
        session.add(Alert(id=alert_id, connector_id=connector_id, external_id="cycle-event",
                          title="Synthetic alert", description="", severity="high", status="resolved",
                          asset_id=asset_id, asset_name="isolated-cycle-node", sanitized_payload={}))
        session.commit()
        item = recovery.create(session, alert_id, RecoveryCreate(
            kind="monitor", metric="availability", observation_minutes=1, max_gap_seconds=30,
            confirmation=alert_id, reason="Human handled this occurrence",
        ), "synthetic-operator")
        origin = datetime.now(timezone.utc) - timedelta(seconds=70)
        stored = session.get(RecoveryVerification, item["id"])
        assert stored is not None
        stored.started_at, stored.deadline_at = origin, origin + timedelta(seconds=60)
        for seconds in (1, 30, 59):
            session.add(MonitoringSnapshot(id=str(uuid4()), asset_id=asset_id, status="online",
                        read_only=True, metrics={}, collected_at=origin + timedelta(seconds=seconds)))
        session.commit()
    vault = CredentialVault(base64.b64encode(os.urandom(32)).decode("ascii"), "synthetic")
    return CycleDatabase(engine, factory, alert_id, asset_id, connector_id, item["id"], vault)


@pytest.fixture
def cycle_database() -> Generator[CycleDatabase, None, None]:
    target = make_url(TEST_DATABASE_URL)
    assert target.get_backend_name() == "postgresql"
    assert target.database == "recovery_cycle_test"
    assert target.host in (None, "")
    assert set(target.query).issubset({"host", "port"})
    socket_path = target.query.get("host")
    assert isinstance(socket_path, str) and socket_path.startswith("/") and "," not in socket_path
    for module in pkgutil.iter_modules(models.__path__):
        importlib.import_module(f"src.models.{module.name}")
    schema = "recovery_cycle_" + uuid4().hex
    engine = create_engine(target, connect_args={
        "connect_timeout": 5,
        "options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=15000",
    }, pool_size=5, max_overflow=0)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        try:
            Base.metadata.create_all(engine)
            yield _seed(engine)
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


def _run_operation(database: CycleDatabase, operation: str, backend_pid: Queue[int]) -> str:
    with database.factory() as session:
        # Keep stale ORM identities alive while the competing transaction holds the row lock.
        cached = (session.get(Alert, database.alert_id),
                  session.get(RecoveryVerification, database.verification_id))
        assert all(item is not None for item in cached)
        backend_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
        try:
            if operation == "reopen":
                connector = session.get(AlertConnector, database.connector_id)
                assert connector is not None
                payload = AlertIngest(external_id="cycle-event", title="Synthetic recurrence",
                                      severity="high", asset_id=database.asset_id)
                result, created = alerts.ingest(session, connector, payload, uuid4().hex * 2, database.vault)
                assert not created and result.status == "open"
                return "reopened"
            if operation == "finish":
                payload = RecoveryAction(expected_version=1, confirmation=database.alert_id,
                                         reason="Human verifies selected recovery evidence")
                return str(recovery.finish(session, database.alert_id, database.verification_id,
                                           payload, "synthetic-operator")["status"])
            result = alerts.update_status(session, database.alert_id, "closed",
                                          "Human closes previous occurrence", "synthetic-operator")
            return result.status
        except AlertConflictError:
            session.rollback()
            return "rejected"


def _wait_for_blocker(engine: Engine, waiting_pid: int, holding_pid: int) -> None:
    deadline = time.monotonic() + 4
    blocked_by_holder = text(
        "WITH RECURSIVE blockers(pid, depth) AS ("
        " SELECT unnest(pg_blocking_pids(:waiting_pid)), 1"
        " UNION ALL"
        " SELECT unnest(pg_blocking_pids(blockers.pid)), blockers.depth + 1"
        " FROM blockers WHERE blockers.depth < 8"
        ") SELECT EXISTS (SELECT 1 FROM blockers WHERE pid = :holding_pid)"
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            # PostgreSQL may queue the second waiter behind the first waiting transaction.
            if connection.scalar(blocked_by_holder, {
                "waiting_pid": waiting_pid, "holding_pid": holding_pid,
            }):
                return
            time.sleep(0.02)
    pytest.fail("The competing operation never waited for the recovery occurrence row lock")


def _hold_until_released(entered: Event, release: Event) -> None:
    entered.set()
    assert release.wait(timeout=8), "The isolated concurrency test did not release its transaction"


def _assert_preserved_history(database: CycleDatabase, expected_status: str) -> None:
    with database.factory() as session:
        alert = session.get(Alert, database.alert_id)
        assert alert is not None and alert.status == "open"
        current = alerts.serialize(session, alert)
        assert current["latest_recovery"] is None
        historical = recovery.history(session, database.alert_id, 999, 1)
        assert historical["items"] == [] and historical["total"] == 1
        assert historical["options"]["active_verification"] is False
        row = session.get(RecoveryVerification, database.verification_id)
        assert row is not None and row.status == expected_status and row.active_alert_id is None
        assert recovery.serialize(session, row)["is_current"] is False
        assert session.scalar(select(func.count()).select_from(RecoveryEvaluation)) == 1
        assert session.scalar(select(func.count()).select_from(AlertNotification).where(
            AlertNotification.kind == "recovery_result")) == 1


def test_recurrence_row_lock_fences_old_evaluation_and_status_update(
    cycle_database: CycleDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = Event(), Event()
    original = recovery.seal_for_recurrence

    def hold_recurrence(session: Session, alert: Alert) -> None:
        _hold_until_released(entered, release)
        original(session, alert)

    monkeypatch.setattr(recovery, "seal_for_recurrence", hold_recurrence)
    reopen_pid: Queue[int] = Queue()
    finish_pid: Queue[int] = Queue()
    close_pid: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=3) as executor:
        recurrence = executor.submit(_run_operation, cycle_database, "reopen", reopen_pid)
        try:
            assert entered.wait(timeout=3)
            holder = reopen_pid.get(timeout=1)
            finish = executor.submit(_run_operation, cycle_database, "finish", finish_pid)
            close = executor.submit(_run_operation, cycle_database, "close", close_pid)
            _wait_for_blocker(cycle_database.engine, finish_pid.get(timeout=3), holder)
            _wait_for_blocker(cycle_database.engine, close_pid.get(timeout=3), holder)
            assert not finish.done() and not close.done()
        finally:
            release.set()
        assert recurrence.result(timeout=8) == "reopened"
        assert finish.result(timeout=8) == "rejected"
        assert close.result(timeout=8) == "rejected"
    _assert_preserved_history(cycle_database, "cancelled")


def test_completed_evaluation_releases_recurrence_without_reusing_success(
    cycle_database: CycleDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = Event(), Event()
    original = recovery.evidence.inspect_evidence

    def hold_evaluation(
        session: Session, item: RecoveryVerification, now: datetime,
    ) -> tuple[str, str, list[dict[str, object]]]:
        _hold_until_released(entered, release)
        return original(session, item, now)

    monkeypatch.setattr(recovery.evidence, "inspect_evidence", hold_evaluation)
    finish_pid: Queue[int] = Queue()
    reopen_pid: Queue[int] = Queue()
    with ThreadPoolExecutor(max_workers=2) as executor:
        finish = executor.submit(_run_operation, cycle_database, "finish", finish_pid)
        try:
            assert entered.wait(timeout=3)
            holder = finish_pid.get(timeout=1)
            recurrence = executor.submit(_run_operation, cycle_database, "reopen", reopen_pid)
            _wait_for_blocker(cycle_database.engine, reopen_pid.get(timeout=3), holder)
            assert not recurrence.done()
        finally:
            release.set()
        assert finish.result(timeout=8) == "verified"
        assert recurrence.result(timeout=8) == "reopened"
    _assert_preserved_history(cycle_database, "verified")
