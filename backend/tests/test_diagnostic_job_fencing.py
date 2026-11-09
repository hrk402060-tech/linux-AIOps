from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import AlertTimeline, RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_grant import DiagnosticGrant
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import alert_diagnostics, diagnostic_jobs, diagnostic_job_claims, diagnostic_job_worker
from src.services.diagnostic_job_guard import DiagnosticCancelled, DiagnosticLeaseLost, guard
from test_diagnostic_jobs import authorized as authorized_fixture


@pytest.fixture
def authorized(client: TestClient, database_session: Session) -> dict:
    return authorized_fixture.__wrapped__(client, database_session)


def test_max_three_attempts_separate_immutable_evidence(client: TestClient, database_session: Session, authorized: dict) -> None:
    ids = []
    for number in range(1, 4):
        database_session.execute(update(DiagnosticJob).values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        database_session.commit()
        claim = diagnostic_job_claims.claim_next(database_session)
        assert claim.attempt_number == number
        ids.append(claim.analysis_id)
        with patch('src.services.alert_diagnostics.collect', side_effect=alert_diagnostics.DiagnosticCollectionFailed(1, 'timeout')):
            assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
        database_session.expire_all()
        job = database_session.get(DiagnosticJob, claim.job_id)
        assert job.status == ('failed' if number == 3 else 'retry_wait')
        assert len(database_session.scalars(select(RootCauseAnalysis).where(RootCauseAnalysis.job_id == job.id)).all()) == number
    assert len(set(ids)) == 3 and diagnostic_job_claims.claim_next(database_session) is None
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id.in_(ids))) == 3


@pytest.mark.parametrize('outcome,code', [('partial', 'timeout'), ('failed', 'authentication_failed'),
                                        ('failed', 'host_key_mismatch'), ('failed', 'invalid_gateway_response')])
def test_partial_and_security_errors_never_retry(client: TestClient, database_session: Session, authorized: dict,
                                               outcome: str, code: str) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    collection = patch('src.services.alert_diagnostics.collect', return_value={
        'status': 'partial', 'summary': 'Partial evidence', 'evidence': {'linux.uptime': '10'}}) if outcome == 'partial' else patch(
        'src.services.alert_diagnostics.collect', side_effect=alert_diagnostics.DiagnosticCollectionFailed(1, code))
    with collection:
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == outcome and job.next_attempt_at is None and job.attempt_count == 1


def test_snapshot_revision_change_before_claim_never_connects(client: TestClient, database_session: Session, authorized: dict) -> None:
    database_session.execute(update(DiagnosticGrant).values(revision=2))
    database_session.commit()
    with patch('src.services.alert_diagnostics.collect') as connection:
        assert diagnostic_job_claims.claim_next(database_session) is None
        connection.assert_not_called()
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, authorized['latest_job']['id'])
    assert job.status == 'failed' and job.grant_revision == 1 and job.next_attempt_at is None


def test_cancel_in_io_retains_accepted_evidence(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    def reading(*_args: object, **_kwargs: object) -> dict:
        with client.app.state.session_factory() as other:
            diagnostic_jobs.cancel(other, authorized['id'], claim.job_id, 'operator', 'cancel during read')
        raise DiagnosticCancelled('Cancelled', 1, {'linux.uptime': '{"uptime_seconds":10}'})
    with patch('src.services.alert_diagnostics.collect', side_effect=reading):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    analysis = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert analysis.status == 'cancelled' and 'uptime_seconds' in analysis.evidence['linux.uptime']
    assert analysis.evidence['grant_revision'] == '1' and analysis.evidence['remediation'] == 'disabled'
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id is None
    event = database_session.scalar(select(AlertTimeline).where(
        AlertTimeline.alert_id == authorized['id'], AlertTimeline.event_type == 'diagnostic_cancel_requested'))
    assert event.operator == 'operator'


def test_old_token_cannot_read_or_release_newer_claim(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    stale = replace(claim, token='stale-token')
    with pytest.raises(DiagnosticLeaseLost):
        guard(client.app.state.session_factory, stale)
    assert not diagnostic_job_worker.finalize(client.app.state.session_factory, stale, {
        'status': 'completed', 'summary': 'forged stale output', 'evidence': {}})
    database_session.expire_all()
    assert database_session.get(DiagnosticSlot, claim.slot_id).lease_token == claim.token
    assert database_session.get(RootCauseAnalysis, claim.analysis_id).status == 'running'


def test_shutdown_stops_claim_and_active_io_without_releasing_slot(client: TestClient, database_session: Session, authorized: dict) -> None:
    stop = Event()
    stop.set()
    assert not diagnostic_job_worker.run_one(client.app.state.session_factory, client.app.state.credential_vault, stop)
    claim = diagnostic_job_claims.claim_next(database_session)
    with pytest.raises(DiagnosticLeaseLost):
        guard(client.app.state.session_factory, claim, stop)
    assert not diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim, stop)
    database_session.expire_all()
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id == claim.job_id


def test_result_notification_failure_rolls_back_entire_finish(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    with patch('src.services.diagnostic_job_state.enqueue_diagnosis', side_effect=SQLAlchemyError('outbox failed')):
        with pytest.raises(SQLAlchemyError):
            diagnostic_job_worker.finalize(client.app.state.session_factory, claim, {
                'status': 'completed', 'error_code': None, 'summary': 'not committed', 'evidence': {},
                'finished_at': datetime.now(timezone.utc)})
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, claim.job_id).status == 'running'
    assert database_session.get(RootCauseAnalysis, claim.analysis_id).finished_at is None
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id == claim.job_id


def test_worker_mid_read_revocation_keeps_previous_evidence(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    def reading(*_args: object, **kwargs: object) -> dict:
        check = kwargs['guard']
        check()
        with client.app.state.session_factory() as other:
            other.execute(update(DiagnosticGrant).values(enabled=False, revision=2))
            other.commit()
        try:
            check()
        except alert_diagnostics.DiagnosticAuthorizationRequired as exc:
            exc.evidence['linux.uptime'] = '{"uptime_seconds":10}'
            raise
        raise AssertionError('Revoked authorization allowed further I/O')
    with patch('src.services.alert_diagnostics.collect', side_effect=reading):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    analysis = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert analysis.error_code == 'diagnostic_authorization_interrupted'
    assert analysis.evidence['linux.uptime'] == '{"uptime_seconds":10}'
    assert analysis.evidence['grant_revision'] == '1' and '停止后续读取' in analysis.summary
    assert database_session.get(DiagnosticJob, claim.job_id).next_attempt_at is None


def test_running_cancel_expiry_does_not_claim_no_execution(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    diagnostic_jobs.cancel(database_session, authorized['id'], claim.job_id, 'operator', 'cancel read')
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    database_session.execute(update(DiagnosticJob).values(lease_expires_at=past))
    database_session.execute(update(DiagnosticSlot).where(DiagnosticSlot.id == claim.slot_id).values(lease_expires_at=past))
    database_session.commit()
    assert diagnostic_job_claims.recover_expired(database_session) == 1
    database_session.expire_all()
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert attempt.status == 'cancelled' and attempt.evidence['execution_performed'] == 'unknown'
    assert database_session.get(DiagnosticJob, claim.job_id).next_attempt_at is None


def test_attempt_history_filters_by_job_before_pagination(client: TestClient, database_session: Session, authorized: dict) -> None:
    alert_id, job_id = authorized['id'], authorized['latest_job']['id']
    diagnostic_jobs.cancel(database_session, alert_id, job_id, 'operator', 'cancel queue')
    response = client.post(f'/api/v1/alerts/{alert_id}/diagnostics', json={'confirmation': alert_id, 'reason': 'new task'})
    assert response.status_code == 202
    newer = response.json()['id']
    diagnostic_jobs.cancel(database_session, alert_id, newer, 'operator', 'cancel new queue')
    history = client.get(f'/api/v1/alerts/{alert_id}/analyses?job_id={job_id}&limit=1').json()
    assert len(history) == 1 and history[0]['job_id'] == job_id and history[0]['attempt_number'] == 1
    assert client.get(f'/api/v1/alerts/{alert_id}/analyses?job_id={job_id}&offset=1&limit=1').json() == []


def test_scheduler_claims_background_job_and_stops_cleanly(client: TestClient, database_session: Session, authorized: dict) -> None:
    import asyncio
    async def run() -> None:
        shutdown = asyncio.Event()
        task = asyncio.create_task(diagnostic_job_worker.run_scheduler(
            client.app.state.session_factory, client.app.state.credential_vault, shutdown))
        try:
            for _ in range(60):
                await asyncio.sleep(0.05)
                with client.app.state.session_factory() as fresh:
                    if fresh.get(DiagnosticJob, authorized['latest_job']['id']).status == 'completed':
                        break
            else:
                raise AssertionError('Background worker did not finish queued task')
        finally:
            shutdown.set()
            await asyncio.wait_for(task, timeout=5)
    with patch('src.services.alert_diagnostics.collect', return_value={
        'status': 'completed', 'summary': 'Background evidence', 'evidence': {'linux.uptime': '10'}}):
        asyncio.run(run())
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, authorized['latest_job']['id']).status == 'completed'
