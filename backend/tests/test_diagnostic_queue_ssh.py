from collections.abc import Callable, Generator
import json

from fastapi.testclient import TestClient
import paramiko
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.alert import AlertTimeline, RootCauseAnalysis
from src.models.alert_notification import AlertNotification
from src.models.diagnostic_grant import DiagnosticGrant, DiagnosticGrantAudit
from src.models.diagnostic_job import DiagnosticJob, DiagnosticSlot
from src.services import diagnostic_job_claims, diagnostic_jobs, diagnostic_job_worker, diagnostic_transport
from src.services.diagnostic_job_guard import DiagnosticClaim
from test_alerts_api import _connector, _ingest
from test_diagnostic_ssh_integration import _configure, restricted_target as restricted_fixture


@pytest.fixture
def restricted_target() -> Generator[tuple, None, None]:
    yield from restricted_fixture.__wrapped__()


@pytest.fixture
def queued_ssh(
    client: TestClient, database_session: Session, restricted_target: tuple,
) -> tuple[str, DiagnosticClaim]:
    asset = _configure(client, database_session, restricted_target)
    connector = _connector(client)
    body, headers = _ingest(client, connector, asset.id)
    response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
    assert response.status_code == 201
    alert = response.json()
    assert alert['latest_analysis'] is None and alert['latest_job']['status'] == 'queued'
    assert alert['latest_job']['grant_revision'] == 1 and restricted_target[3].commands == []
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None and claim.grant_revision == 1 and claim.attempt_number == 1
    return alert['id'], claim


def finished(
    session: Session, claim: DiagnosticClaim, status: str,
) -> tuple[RootCauseAnalysis, AlertNotification]:
    session.expire_all()
    job = session.get(DiagnosticJob, claim.job_id)
    analysis = session.get(RootCauseAnalysis, claim.analysis_id)
    assert job.status == status and job.active_alert_id is None and job.next_attempt_at is None
    assert job.attempt_count == 1 and job.finished_at is not None
    assert analysis.status == status and analysis.finished_at is not None
    assert analysis.job_id == job.id and analysis.attempt_number == 1
    assert analysis.evidence['grant_revision'] == '1' and analysis.evidence['remediation'] == 'disabled'
    assert session.get(DiagnosticSlot, claim.slot_id).job_id is None
    notices = session.scalars(select(AlertNotification).where(AlertNotification.analysis_id == analysis.id)).all()
    assert len(notices) == 1 and status in notices[0].body
    assert '恢复未经验证' in notices[0].body
    audit = session.scalar(select(DiagnosticGrantAudit).where(
        DiagnosticGrantAudit.asset_id == claim.asset_id, DiagnosticGrantAudit.action == 'read_' + status))
    assert audit is not None and audit.revision == 1
    return analysis, notices[0]


def test_real_queued_ssh_finishes_fixed_read_evidence_and_result_outbox(
    client: TestClient, database_session: Session, restricted_target: tuple,
    queued_ssh: tuple[str, DiagnosticClaim],
) -> None:
    claim = queued_ssh[1]
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                             client.app.state.credential_vault, claim)
    analysis, _ = finished(database_session, claim, 'completed')
    assert 'uptime_seconds' in json.loads(analysis.evidence['linux.uptime'])
    assert 'memory_kib' in json.loads(analysis.evidence['linux.memory'])
    assert restricted_target[3].commands == [f'{diagnostic_transport.PROTOCOL}:handshake',
        f'{diagnostic_transport.PROTOCOL}:linux.uptime', f'{diagnostic_transport.PROTOCOL}:linux.memory']
    assert all(channel.closed for channel in restricted_target[3].channels)


def test_real_queued_ssh_mid_read_revoke_keeps_revision_and_prior_evidence(
    client: TestClient, database_session: Session, restricted_target: tuple,
    queued_ssh: tuple[str, DiagnosticClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = queued_ssh[1]
    original = diagnostic_transport.request
    connections: list[paramiko.SSHClient] = []
    def revoke_after_first(ssh: paramiko.SSHClient, action: str, deadline: float,
                           authorize: Callable[[], None]) -> dict[str, object]:
        connections.append(ssh)
        result = original(ssh, action, deadline, authorize)
        if action == 'linux.uptime':
            with client.app.state.session_factory() as other:
                other.execute(update(DiagnosticGrant).where(DiagnosticGrant.asset_id == claim.asset_id)
                              .values(enabled=False, revision=2))
                other.commit()
        return result
    monkeypatch.setattr(diagnostic_transport, 'request', revoke_after_first)
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                             client.app.state.credential_vault, claim)
    analysis, _ = finished(database_session, claim, 'failed')
    assert analysis.error_code == 'diagnostic_authorization_interrupted'
    assert 'uptime_seconds' in json.loads(analysis.evidence['linux.uptime'])
    assert 'linux.memory' not in analysis.evidence
    assert restricted_target[3].commands == [f'{diagnostic_transport.PROTOCOL}:handshake',
        f'{diagnostic_transport.PROTOCOL}:linux.uptime']
    assert connections and all(ssh.get_transport() is None for ssh in connections)
    assert all(channel.closed for channel in restricted_target[3].channels)


def test_real_queued_ssh_cancel_keeps_slot_until_close_and_preserves_actor(
    client: TestClient, database_session: Session, restricted_target: tuple,
    queued_ssh: tuple[str, DiagnosticClaim], monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert_id, claim = queued_ssh
    original = diagnostic_transport.request
    connections: list[paramiko.SSHClient] = []
    def cancel_after_first(ssh: paramiko.SSHClient, action: str, deadline: float,
                           authorize: Callable[[], None]) -> dict[str, object]:
        connections.append(ssh)
        result = original(ssh, action, deadline, authorize)
        if action == 'linux.uptime':
            with client.app.state.session_factory() as other:
                job = diagnostic_jobs.cancel(other, alert_id, claim.job_id,
                                             'demo-operator', 'human cancels actual read')
                assert job.status == 'running' and job.cancellation_requested_at is not None
                assert diagnostic_jobs.serialize(job)['display_status'] == 'cancelling'
                assert other.get(DiagnosticSlot, claim.slot_id).job_id == claim.job_id
                assert ssh.get_transport() is not None and ssh.get_transport().is_active()
        return result
    monkeypatch.setattr(diagnostic_transport, 'request', cancel_after_first)
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory,
                                             client.app.state.credential_vault, claim)
    analysis, _ = finished(database_session, claim, 'cancelled')
    assert analysis.error_code == 'cancelled'
    assert 'uptime_seconds' in json.loads(analysis.evidence['linux.uptime'])
    assert 'linux.memory' not in analysis.evidence
    assert restricted_target[3].commands == [f'{diagnostic_transport.PROTOCOL}:handshake',
        f'{diagnostic_transport.PROTOCOL}:linux.uptime']
    assert connections and all(ssh.get_transport() is None for ssh in connections)
    event = database_session.scalar(select(AlertTimeline).where(
        AlertTimeline.alert_id == alert_id, AlertTimeline.event_type == 'diagnostic_cancel_requested'))
    assert event is not None and event.operator == 'demo-operator'
    assert 'human cancels actual read' in event.note
    assert all(channel.closed for channel in restricted_target[3].channels)
