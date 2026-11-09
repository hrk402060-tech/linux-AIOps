from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.models.alert import RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_grant import DiagnosticGrant
from src.models.diagnostic_job import DiagnosticJob
from src.services import diagnostic_job_claims, diagnostic_job_worker, alert_diagnostics
from test_alerts_api import _connector, _ingest
from test_diagnostic_grants import login_role
from test_kubernetes_grants import payload


@pytest.fixture
def authorized_kubernetes(client: TestClient, database_session: Session) -> dict[str, object]:
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


def test_kubernetes_attempt_never_uses_ssh_and_commits_outbox(
    client: TestClient, database_session: Session, authorized_kubernetes: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    with (patch('src.services.ssh_host_key_registry.require_registered') as host_key,
          patch('src.services.alert_diagnostics.collect') as ssh,
          patch('src.services.kubernetes_diagnostics.collect', return_value={
              'status': 'completed', 'summary': '已取得命名空间状态证据，根因待核实',
              'evidence': {'kubernetes.pods:production': '{"items":[]}', 'read_only': 'true',
                           'grant_revision': '1', 'remediation': 'disabled'},
          }) as kubernetes):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                   client.app.state.credential_vault, claim)
    host_key.assert_not_called()
    ssh.assert_not_called()
    assert kubernetes.call_args.kwargs['expected_revision'] == 1
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, claim.job_id)
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == 'completed'
    assert attempt.policy_version == 'readonly-kubernetes-2026.10.15'
    assert attempt.evidence['remediation'] == 'disabled'
    assert database_session.scalar(select(func.count()).select_from(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) == 1


def test_kubernetes_revision_invalidated_before_claim_never_connects(
    client: TestClient, database_session: Session, authorized_kubernetes: dict[str, object],
) -> None:
    database_session.execute(update(DiagnosticGrant).values(revision=2))
    database_session.commit()
    with patch('src.services.kubernetes_diagnostics.collect') as network:
        assert diagnostic_job_claims.claim_next(database_session) is None
    network.assert_not_called()
    database_session.expire_all()
    job = database_session.get(DiagnosticJob, authorized_kubernetes['latest_job']['id'])
    assert job.status == 'failed'


def test_kubernetes_revocation_retains_safe_evidence_and_notifies(
    client: TestClient, database_session: Session, authorized_kubernetes: dict[str, object],
) -> None:
    claim = diagnostic_job_claims.claim_next(database_session)
    def reading(*args: object, **kwargs: object) -> dict[str, object]:
        check = kwargs['guard']
        check()
        with client.app.state.session_factory() as other:
            other.execute(update(DiagnosticGrant).values(enabled=False, revision=2))
            other.commit()
        try:
            check()
        except alert_diagnostics.DiagnosticAuthorizationRequired as exc:
            exc.evidence['kubernetes.pods:production'] = '{"items":[]}'
            raise
        raise AssertionError('Revoked Kubernetes read was accepted')
    with patch('src.services.kubernetes_diagnostics.collect', side_effect=reading):
        assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                                   client.app.state.credential_vault, claim)
    database_session.expire_all()
    attempt = database_session.get(RootCauseAnalysis, claim.analysis_id)
    assert attempt.error_code == 'diagnostic_authorization_interrupted'
    assert 'kubernetes.pods:production' in attempt.evidence
    assert database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == attempt.id)) is not None
