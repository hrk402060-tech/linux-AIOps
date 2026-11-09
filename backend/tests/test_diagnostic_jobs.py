from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.models.alert import Alert, RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import diagnostic_jobs, diagnostic_job_worker, diagnostic_job_claims
from test_alerts_api import _connector, _ingest
from test_diagnostic_grants import enroll, identity as identity_fixture, login_role


@pytest.fixture
def authorized(client: TestClient, database_session: Session) -> dict:
    payload = enroll(client, database_session, identity_fixture.__wrapped__()[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    connector = _connector(client)
    body, headers = _ingest(client, connector, payload['asset_id'])
    with patch('src.services.alert_diagnostics.collect') as network:
        result = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
        network.assert_not_called()
    assert result.status_code == 201
    assert result.json()['latest_analysis'] is None
    assert result.json()['latest_job']['status'] == 'queued'
    return result.json()


def test_claim_finish_seals_attempt_and_notifies(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None and claim.attempt_number == 1
    with patch('src.services.alert_diagnostics.collect', return_value={
        'status': 'completed', 'summary': 'Evidence only', 'evidence': {'linux.uptime': '10'},
    }):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == 'completed' and job.active_alert_id is None
    analysis = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert analysis.finished_at is not None and analysis.status == 'completed'
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == analysis.id)) == 1
    assert all(slot.job_id is None for slot in database_session.scalars(select(DiagnosticSlot)))


def test_active_conflict_confirmation_roles_and_cancel(client: TestClient, database_session: Session, authorized: dict) -> None:
    alert_id = authorized['id']
    endpoint = f'/api/v1/alerts/{alert_id}/diagnostics'
    assert client.post(endpoint, json={'confirmation': 'wrong', 'reason': 'test reason'}).status_code == 400
    assert client.post(endpoint, json={'confirmation': alert_id, 'reason': 'test reason'}).status_code == 409
    job_id = authorized['latest_job']['id']
    cancel = f'/api/v1/alerts/{alert_id}/diagnostic-jobs/{job_id}/cancel'
    login_role(client, database_session, 'auditor')
    assert client.post(cancel, json={'confirmation': alert_id, 'reason': 'test reason'}).status_code == 403
    login_role(client, database_session, 'operator')
    response = client.post(cancel, json={'confirmation': f' {alert_id} ', 'reason': 'cancel test'})
    assert response.status_code == 202 and response.json()['status'] == 'cancelled'
    assert client.post(endpoint, json={'confirmation': alert_id, 'reason': 'manual retry'}).status_code == 202
    assert client.get(f'/api/v1/alerts/{alert_id}/diagnostic-jobs?limit=1').json()['total'] == 2


def test_running_cancel_retains_slot_until_cleanup(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    job = diagnostic_jobs.cancel(database_session, authorized['id'], claim.job_id, 'operator', 'stop test')
    assert job.status == 'running' and job.cancellation_requested_at is not None
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id == claim.job_id
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, claim.job_id).status == 'cancelled'
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id is None


def test_expired_claim_recovery_fences_old_worker(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    database_session.execute(update(DiagnosticJob).values(lease_expires_at=past))
    database_session.execute(update(DiagnosticSlot).where(DiagnosticSlot.id == claim.slot_id).values(lease_expires_at=past))
    database_session.commit()
    assert diagnostic_job_claims.recover_expired(database_session) == 1
    assert not diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == 'retry_wait' and job.last_error_code == 'worker_interrupted'
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert attempt.status == 'failed' and attempt.finished_at is not None
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) == 1


def test_orphan_running_job_without_slot_is_recovered(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    slot = database_session.get(DiagnosticSlot, claim.slot_id)
    database_session.delete(slot)
    database_session.commit()
    assert diagnostic_job_claims.recover_expired(database_session) == 1
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == 'retry_wait' and job.last_error_code == 'worker_interrupted'
    assert not diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)


def test_running_job_with_mismatched_slot_is_recovered(client: TestClient, database_session: Session, authorized: dict) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    database_session.execute(update(DiagnosticSlot).where(DiagnosticSlot.id == claim.slot_id).values(
        lease_token='stale-token'))
    database_session.commit()
    assert diagnostic_job_claims.recover_expired(database_session) == 1
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == 'retry_wait' and job.last_error_code == 'worker_interrupted'
    assert database_session.get(DiagnosticSlot, claim.slot_id).job_id is None


def test_two_slots_and_retry_is_bounded(client: TestClient, database_session: Session, authorized: dict) -> None:
    alert = database_session.get(Alert, authorized['id'])
    for number in range(2):
        clone = Alert(id=f'parallel-{number}', connector_id=alert.connector_id, external_id=f'parallel-{number}',
                      title='Parallel', description='', severity='high', status='open', asset_id=alert.asset_id,
                      asset_name=alert.asset_name, sanitized_payload={})
        database_session.add(clone)
        diagnostic_jobs.enqueue(database_session, clone, 'automatic', 'system', '')
        database_session.commit()
    first = diagnostic_job_claims.claim_next(database_session)
    second = diagnostic_job_claims.claim_next(database_session)
    assert first is not None and second is not None
    assert diagnostic_job_claims.claim_next(database_session) is None
    with patch('src.services.alert_diagnostics.collect', side_effect=__import__(
        'src.services.alert_diagnostics', fromlist=['DiagnosticCollectionFailed']).DiagnosticCollectionFailed(1, 'timeout')):
        diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, first)
    database_session.expire_all()
    assert database_session.get(DiagnosticJob, first.job_id).status == 'retry_wait'


def test_automatic_overflow_keeps_alert_and_failed_outbox(client: TestClient, database_session: Session, authorized: dict) -> None:
    with patch('src.services.diagnostic_jobs.MAX_ACTIVE', 1):
        alert = database_session.get(Alert, authorized['id'])
        clone = Alert(id='overflow', connector_id=alert.connector_id, external_id='overflow', title='Overflow',
                      description='', severity='high', status='open', asset_id=alert.asset_id,
                      asset_name=alert.asset_name, sanitized_payload={})
        database_session.add(clone)
        job = diagnostic_jobs.enqueue(database_session, clone, 'automatic', 'system', '')
        database_session.commit()
        assert job.status == 'failed' and job.last_error_code == 'queue_capacity_exceeded'
        assert database_session.get(Alert, 'overflow') is not None
        assert database_session.scalar(select(AlertNotification).where(AlertNotification.alert_id == 'overflow')) is not None
