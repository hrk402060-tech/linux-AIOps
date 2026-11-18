import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.models.alert import Alert, RootCauseAnalysis
from src.models.asset import Asset, AssetEnvironment, AssetLifecycleStatus, OsFamily
from src.models.credential import Credential, CredentialType
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.models.scan_task import ScanStatus, ScanTask, ScanType
from src.services import (
    diagnostic_job_claims, diagnostic_job_guard, diagnostic_job_state, diagnostic_job_worker,
    monitoring, scan_tasks,
)
from test_alerts_api import _connector
from test_settings_runtime import _save


def _asset(session, name: str, **values: object) -> Asset:
    item = Asset(
        id=str(uuid4()), name=name, host="192.0.2.10",
        os_family=OsFamily.UBUNTU, environment=AssetEnvironment.TEST, **values,
    )
    session.add(item)
    session.flush()
    return item


def _queue(session, connector_id: str, asset: Asset, count: int) -> list[DiagnosticJob]:
    jobs = []
    for _ in range(count):
        identifier = str(uuid4())
        alert = Alert(
            id=identifier, connector_id=connector_id, external_id=identifier,
            title="Runtime admission", description="", severity="high", status="open",
            asset_id=asset.id, asset_name=asset.name, sanitized_payload={},
        )
        session.add(alert)
        session.flush()
        job = DiagnosticJob(
            id=str(uuid4()), alert_id=alert.id, active_alert_id=alert.id,
            asset_id=asset.id, grant_revision=1, status="queued",
            trigger_type="automatic", operator="system", reason="runtime regression",
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    return jobs


def test_saved_concurrency_admits_more_than_two_and_reduction_blocks_new_claims(
    client, database_session, monkeypatch,
) -> None:
    _save(client, "tasks", max_concurrency=3)
    connector = _connector(client)
    asset = _asset(database_session, "runtime-admission")
    _queue(database_session, connector["id"], asset, 4)
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_job_access, "require_operator_scope", Mock())
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_grants, "require_grant", Mock())

    claims = [diagnostic_job_claims.claim_next(database_session) for _ in range(3)]
    assert all(claim is not None for claim in claims)
    assert database_session.scalar(select(func.count()).select_from(DiagnosticSlot).where(
        DiagnosticSlot.job_id.is_not(None),
    )) == 3
    assert diagnostic_job_claims.claim_next(database_session) is None

    _save(client, "tasks", max_concurrency=1)
    assert diagnostic_job_claims.claim_next(database_session) is None
    assert database_session.scalar(select(func.count()).select_from(DiagnosticSlot).where(
        DiagnosticSlot.job_id.is_not(None),
    )) == 3


@pytest.mark.parametrize("failure_policy,expected", [("stop", "failed"), ("continue", "retry_wait")])
def test_saved_failure_policy_controls_diagnostic_retry(
    client, database_session, failure_policy: str, expected: str,
) -> None:
    _save(client, "tasks", failure_policy=failure_policy)
    connector = _connector(client)
    asset = _asset(database_session, "runtime-retry")
    job = _queue(database_session, connector["id"], asset, 1)[0]
    analysis = RootCauseAnalysis(
        id=str(uuid4()), alert_id=job.alert_id, asset_id=asset.id, job_id=job.id,
        attempt_number=1, status="failed", trigger_type="automatic",
        policy_version="runtime-regression", summary="transport timed out", evidence={},
        operator="system", trace_id=str(uuid4()), error_code="timeout",
    )
    job.attempt_count = 1
    database_session.add(analysis)
    diagnostic_job_state.advance(job, analysis, datetime.now(timezone.utc))
    database_session.commit()
    assert job.status == expected
    assert (job.next_attempt_at is not None) == (failure_policy == "continue")


@pytest.mark.parametrize("failure_policy,count", [("stop", 1), ("continue", 2)])
def test_scan_failure_policy_stops_or_continues_remaining_hosts(
    client, database_session, monkeypatch, failure_policy: str, count: int,
) -> None:
    _save(client, "tasks", failure_policy=failure_policy)
    assets = [_asset(database_session, f"runtime-scan-{index}") for index in range(2)]
    task = ScanTask(
        id=str(uuid4()), name="runtime-failure-policy", scan_type=ScanType.COMPREHENSIVE,
        status=ScanStatus.SCANNING, asset_ids=[item.id for item in assets], asset_snapshot=[],
    )
    database_session.add(task)
    database_session.commit()
    collector = Mock(return_value={"asset_name": "unreachable", "status": "failed", "findings": []})
    monkeypatch.setattr(scan_tasks, "_collect_asset", collector)
    monkeypatch.setattr(scan_tasks, "require_background_assets", Mock())

    results, findings = scan_tasks._collect_targets(
        database_session, task, client.app.state.credential_vault,
    )
    assert len(results) == collector.call_count == count
    assert findings == []


def test_scheduler_reduction_preserves_running_threads_until_they_finish(monkeypatch) -> None:
    async def scenario() -> None:
        started = Event()
        release = Event()
        guard = Lock()
        stops = []
        active = 0
        maximum = 2
        shutdown = asyncio.Event()

        def run_one(_factory, _vault, stop, _health):
            nonlocal active
            with guard:
                active += 1
                stops.append(stop)
                if active == 2:
                    started.set()
            try:
                assert release.wait(5), "Scheduler did not allow the test workers to finish"
                return True
            finally:
                with guard:
                    active -= 1

        monkeypatch.setattr(diagnostic_job_worker, "run_one", run_one)
        scheduler = asyncio.create_task(diagnostic_job_worker.run_scheduler(
            None, None, shutdown, worker_count=lambda: maximum,
        ))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            maximum = 1
            await asyncio.sleep(1.1)
            assert active == 2
            assert all(not item.is_set() for item in stops)
            shutdown.set()
            await asyncio.sleep(0.02)
            assert not scheduler.done()
        finally:
            shutdown.set()
            release.set()
            await asyncio.wait_for(scheduler, timeout=2)
        assert active == 0

    asyncio.run(scenario())


def test_monitoring_scheduler_reads_saved_scope_and_excludes_disabled_assets(
    client, database_session, monkeypatch,
) -> None:
    _save(client, "connection", collection_scope="production_assets")
    credential = Credential(
        id=str(uuid4()), name="runtime-monitor", credential_type=CredentialType.PASSWORD,
        username="monitor", encrypted_secret=b"encrypted", nonce=b"x" * 12, key_version="test-v1",
    )
    database_session.add(credential)
    database_session.flush()
    production = _asset(database_session, "production", credential_id=credential.id)
    production.environment = AssetEnvironment.PRODUCTION
    _asset(database_session, "test", credential_id=credential.id)
    disabled = _asset(database_session, "disabled", credential_id=credential.id)
    disabled.environment = AssetEnvironment.PRODUCTION
    disabled.lifecycle_status = AssetLifecycleStatus.DISABLED
    _asset(database_session, "missing-credential")
    database_session.commit()
    collected = []

    async def scenario() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def collect(_factory, _vault, identifier):
            collected.append(identifier)
            loop.call_soon_threadsafe(stop.set)

        monkeypatch.setattr(monitoring, "_collect_asset_by_id", collect)
        await asyncio.wait_for(monitoring.run_scheduler(
            client.app.state.session_factory, client.app.state.credential_vault,
            0, stop,
        ), timeout=2)

    asyncio.run(scenario())
    assert collected == [production.id]


def test_monitoring_interval_reduction_is_observed_without_waiting_old_interval(
    client, monkeypatch,
) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        getter_calls = 0

        def interval():
            nonlocal getter_calls
            getter_calls += 1
            if getter_calls == 2:
                stop.set()
            return 86400 if getter_calls == 1 else 60

        await asyncio.wait_for(monitoring.run_scheduler(
            client.app.state.session_factory, client.app.state.credential_vault,
            interval, stop,
        ), timeout=2)
        assert getter_calls >= 2

    asyncio.run(scenario())


def test_diagnostic_guard_renews_both_leases_without_resetting_execution_clock(
    client, database_session, monkeypatch,
) -> None:
    connector = _connector(client)
    asset = _asset(database_session, "runtime-renewal")
    _queue(database_session, connector["id"], asset, 1)
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_job_access, "require_operator_scope", Mock())
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_grants, "require_grant", Mock())
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    started = datetime.now(timezone.utc) - timedelta(seconds=10)
    short_expiry = datetime.now(timezone.utc) + timedelta(seconds=10)
    job = database_session.get(DiagnosticJob, claim.job_id)
    slot = database_session.get(DiagnosticSlot, claim.slot_id)
    job.updated_at, job.lease_expires_at, slot.lease_expires_at = started, short_expiry, short_expiry
    database_session.commit()

    diagnostic_job_guard.guard(client.app.state.session_factory, claim)
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    slot = database_session.get(DiagnosticSlot, claim.slot_id)
    assert diagnostic_job_guard.utc(job.lease_expires_at) > short_expiry + timedelta(seconds=60)
    assert job.lease_expires_at == slot.lease_expires_at
    assert diagnostic_job_guard.utc(job.updated_at) == started


def test_diagnostic_execution_timeout_is_failed_and_never_labeled_manual_cancel(
    client, database_session, monkeypatch,
) -> None:
    _save(client, "tasks", execution_timeout_seconds=60)
    connector = _connector(client)
    asset = _asset(database_session, "runtime-timeout")
    _queue(database_session, connector["id"], asset, 1)
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_job_access, "require_operator_scope", Mock())
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_grants, "require_grant", Mock())
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    job = database_session.get(DiagnosticJob, claim.job_id)
    job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    database_session.commit()
    collect = Mock()
    monkeypatch.setattr(diagnostic_job_worker.alerts, "_collect_analysis", collect)

    assert diagnostic_job_worker.execute_claim(
        client.app.state.session_factory, client.app.state.credential_vault, claim,
    )
    collect.assert_not_called()
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    analysis = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == analysis.status == "failed"
    assert job.last_error_code == analysis.error_code == "execution_timeout"
    assert "人工取消" not in analysis.summary
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id is None


def test_expired_diagnostic_lease_cannot_be_revived_by_guard(
    client, database_session, monkeypatch,
) -> None:
    connector = _connector(client)
    asset = _asset(database_session, "runtime-expired")
    _queue(database_session, connector["id"], asset, 1)
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_job_access, "require_operator_scope", Mock())
    monkeypatch.setattr(diagnostic_job_claims.diagnostic_grants, "require_grant", Mock())
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    job = database_session.get(DiagnosticJob, claim.job_id)
    slot = database_session.get(DiagnosticSlot, claim.slot_id)
    job.lease_expires_at, slot.lease_expires_at = expired, expired
    database_session.commit()
    with pytest.raises(diagnostic_job_guard.DiagnosticLeaseLost):
        diagnostic_job_guard.guard(client.app.state.session_factory, claim)
    database_session.expire_all()
    assert diagnostic_job_guard.utc(database_session.get(DiagnosticJob, claim.job_id).lease_expires_at) == expired
