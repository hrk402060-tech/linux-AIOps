from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.models.alert import RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import alert_diagnostics, diagnostic_job_claims, diagnostic_job_worker
from test_alerts_api import _connector, _ingest
from test_diagnostic_grants import login_role
from test_docker_grants import payload


@pytest.fixture
def authorized_docker(client: TestClient, database_session: Session) -> dict[str, object]:
    data = payload(client)
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=data).status_code == 201
    connector = _connector(client)
    body, headers = _ingest(client, connector, str(data['asset_id']))
    with patch('paramiko.SSHClient') as ssh:
        response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
    ssh.assert_not_called()
    assert response.status_code == 201
    assert response.json()['latest_job']['status'] == 'queued'
    return response.json()


def collected() -> dict[str, object]:
    return {
        'status': 'completed', 'summary': '取得容器只读状态，根因仍需人工核实',
        'evidence': {'docker.containers': '{"items":[]}', 'read_only': 'true',
                     'grant_revision': '1', 'remediation': 'disabled'},
    }


def test_docker_dispatch_never_uses_ssh_and_commits_required_outbox(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    with (patch('src.services.ssh_host_key_registry.require_registered') as host_key,
          patch('src.services.alert_diagnostics.collect') as ssh,
          patch('src.services.kubernetes_diagnostics.collect') as kubernetes,
          patch('src.services.docker_diagnostics.collect', return_value=collected()) as docker):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                   client.app.state.credential_vault, claim)
    host_key.assert_not_called()
    ssh.assert_not_called()
    kubernetes.assert_not_called()
    assert docker.call_args.kwargs['expected_revision'] == 1
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == 'completed'
    assert attempt.policy_version == 'readonly-docker-2026.10.15'
    assert attempt.evidence['remediation'] == 'disabled'
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) == 1


@pytest.mark.parametrize('mutation', ['revision', 'expired', 'revoked'])
def test_invalid_authorization_before_claim_never_connects(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object], mutation: str,
) -> None:
    values = {'revision': 2} if mutation == 'revision' else (
        {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)} if mutation == 'expired'
        else {'enabled': False})
    database_session.execute(update(DiagnosticGrant).values(**values))
    database_session.commit()
    with patch('src.services.docker_diagnostics.collect') as network:
        assert diagnostic_job_claims.claim_next(database_session) is None
    network.assert_not_called()
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, authorized_docker['latest_job']['id'])
    attempt = database_session.get(RootCauseAnalysis, job.current_analysis_id)
    assert job.status == 'failed' and attempt.error_code == 'diagnostic_authorization_required'
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) is not None


def test_revocation_between_reads_keeps_safe_evidence_and_notifies(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None

    def reading(*args: object, **kwargs: object) -> dict[str, object]:
        check = kwargs['guard']
        check()
        with client.app.state.session_factory() as other:
            other.execute(update(DiagnosticGrant).values(enabled=False, revision=2))
            other.commit()
        try:
            check()
        except alert_diagnostics.DiagnosticAuthorizationRequired as exc:
            exc.evidence['docker.containers'] = '{"items":[]}'
            raise
        raise AssertionError('Revoked Docker read was accepted')

    with patch('src.services.docker_diagnostics.collect', side_effect=reading):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                   client.app.state.credential_vault, claim)
    database_session.expire_all()
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert attempt.error_code == 'diagnostic_authorization_interrupted'
    assert 'docker.containers' in attempt.evidence
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) is not None


def test_cancellation_before_collection_never_connects(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    database_session.execute(update(DiagnosticJob).where(DiagnosticJob.id == claim.job_id).values(
        cancellation_requested_at=datetime.now(timezone.utc)))
    database_session.commit()
    with patch('src.services.docker_diagnostics.collect') as network:
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                   client.app.state.credential_vault, claim)
    network.assert_not_called()
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    assert job.status == 'cancelled'
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == claim.analysis_id)) is not None


def test_expired_lease_cannot_commit_late_docker_output(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None

    def late_result(*args: object, **kwargs: object) -> dict[str, object]:
        with client.app.state.session_factory() as other:
            expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            other.execute(update(DiagnosticJob).where(DiagnosticJob.id == claim.job_id)
                          .values(lease_expires_at=expired))
            other.execute(update(DiagnosticSlot).where(DiagnosticSlot.id == claim.slot_id)
                          .values(lease_expires_at=expired))
            other.commit()
        return collected()

    with patch('src.services.docker_diagnostics.collect', side_effect=late_result):
        assert not diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                       client.app.state.credential_vault, claim)
    database_session.expire_all()
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert attempt.status == 'running' and attempt.finished_at is None
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) == 0


def test_outbox_failure_rolls_back_docker_result_and_read_audit(
    client: TestClient, database_session: Session, authorized_docker: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    before = database_session.scalar(select(func.count()).select_from(DiagnosticGrantAudit))
    with (patch('src.services.docker_diagnostics.collect', return_value=collected()),
          patch('src.services.diagnostic_job_state.enqueue_diagnosis',
                side_effect=SQLAlchemyError('outbox unavailable'))):
        assert not diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                       client.app.state.credential_vault, claim)
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == 'running' and attempt.status == 'running'
    assert attempt.finished_at is None and attempt.evidence == {}
    assert database_session.scalar(select(func.count()).select_from(DiagnosticGrantAudit)) == before
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) == 0
