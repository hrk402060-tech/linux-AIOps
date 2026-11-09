from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.models.alert_notification import AlertNotification
from src.models.asset import Asset
from src.models.diagnostic_grant import DiagnosticGrant
from src.services import diagnostic_grants
from test_diagnostic_grants import enroll, identity as identity_fixture, login_role
from test_alerts_api import _connector, _ingest
from src.services import diagnostic_job_claims, diagnostic_job_worker


def _finish(client: TestClient, alert_id: str) -> dict:
    with client.app.state.session_factory() as session:
        claim = diagnostic_job_claims.claim_next(session)
    assert claim is not None
    assert diagnostic_job_worker.execute_claim(client.app.state.session_factory, client.app.state.credential_vault, claim)
    return client.get(f'/api/v1/alerts/{alert_id}').json()['latest_analysis']


@pytest.fixture
def identity() -> tuple:
    return identity_fixture.__wrapped__()


@pytest.mark.parametrize('outcome', ['completed', 'partial', 'failed'])
def test_all_finished_diagnostics_keep_notification_and_read_audit(
    client: TestClient, database_session: Session, identity: tuple, outcome: str,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    connector = _connector(client)
    body, headers = _ingest(client, connector, payload['asset_id'])
    with patch('src.services.alert_diagnostics.collect', return_value={
        'status': outcome, 'summary': 'Evidence only, root cause unconfirmed',
        'evidence': {'linux.uptime': '{"uptime_seconds":10}', 'remediation': 'disabled'},
    }):
        response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
        analysis = _finish(client, response.json()['id'])
    assert response.status_code == 201
    assert analysis['status'] == outcome
    notification = database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == analysis['id']))
    assert notification is not None and outcome in notification.body
    assert '恢复未经验证' in notification.body
    audit = client.get(f"/api/v1/diagnostic-grants/{payload['asset_id']}/audit").json()['items']
    assert audit[0]['action'] == 'read_' + outcome
    unified = client.get('/api/v1/audit-log?source=diagnostic_authorization').json()
    assert any(item['action'] == 'read_' + outcome for item in unified['items'])
    timeline = client.get(f"/api/v1/alerts/{response.json()['id']}/timeline").json()
    final = next(item for item in timeline if item['event_type'] == 'diagnostic_finished')
    assert ('部分' in final['note']) is (outcome == 'partial')


def test_diagnostic_authorization_blocks_physical_asset_cleanup(
    client: TestClient, database_session: Session, identity: tuple,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    preview = client.get(f"/api/v1/assets/{payload['asset_id']}/cleanup-preview").json()
    assert preview['allowed'] is False and '只读诊断' in preview['reason']
    assert client.post(f"/api/v1/assets/{payload['asset_id']}/cleanup", json={
        'confirmation': preview['confirmation_phrase'], 'reason': 'test cleanup',
    }).status_code == 409
    assert database_session.get(Asset, payload['asset_id']) is not None


@pytest.mark.parametrize('change', ['revoked', 'revision', 'expiry'])
def test_new_session_detects_authorization_change(
    client: TestClient, database_session: Session, identity: tuple, change: str,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    asset = database_session.get(Asset, payload['asset_id'])
    before = diagnostic_grants.require_grant(database_session, asset)
    with Session(database_session.get_bind()) as other:
        values = {'enabled': False} if change == 'revoked' else {'revision': 2} if change == 'revision' else {
            'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}
        other.execute(update(DiagnosticGrant).where(DiagnosticGrant.asset_id == asset.id).values(**values))
        other.commit()
    with pytest.raises(diagnostic_grants.DiagnosticAuthorizationError):
        diagnostic_grants.require_grant(database_session, asset, before.revision)


def test_metadata_survives_full_evidence_budget() -> None:
    from src.services.alert_diagnostics import sanitize_evidence
    result = sanitize_evidence({'linux.uptime': 'x' * 32000, 'grant_revision': '1',
                               'action_errors': '{"linux.memory":"timeout"}', 'read_only': 'true'})
    assert result['grant_revision'] == '1' and 'timeout' in result['action_errors']
    assert result['read_only'] == 'true'
    assert sum(map(len, result.values())) <= 16000


def test_read_audit_records_attempted_not_finish_revision(
    client: TestClient, database_session: Session, identity: tuple,
) -> None:
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    client.post('/api/v1/diagnostic-grants', json=payload)
    database_session.execute(update(DiagnosticGrant).values(revision=2))
    diagnostic_grants.record_read(database_session, payload['asset_id'], 'diagnostic-admin', 'demo-trace', 'failed', '1')
    database_session.commit()
    audit = client.get(f"/api/v1/diagnostic-grants/{payload['asset_id']}/audit").json()['items']
    assert audit[0]['revision'] == 1 and audit[0]['action'] == 'read_failed'


@pytest.mark.parametrize('prior_evidence', [{}, {'linux.uptime': '{"uptime_seconds":10}'}])
def test_interrupted_authorization_keeps_truthful_evidence_and_result_notification(
    client: TestClient, database_session: Session, identity: tuple, prior_evidence: dict[str, str],
) -> None:
    from src.services.alert_diagnostics import DiagnosticAuthorizationRequired
    payload = enroll(client, database_session, identity[0])
    login_role(client, database_session, 'admin')
    assert client.post('/api/v1/diagnostic-grants', json=payload).status_code == 201
    connector = _connector(client)
    body, headers = _ingest(client, connector, payload['asset_id'])
    with patch('src.services.alert_diagnostics.collect', side_effect=DiagnosticAuthorizationRequired(
        'Authorization interrupted', 1, prior_evidence)):
        response = client.post(f"/api/v1/alert-ingest/{connector['id']}", content=body, headers=headers)
        analysis = _finish(client, response.json()['id'])
    assert response.status_code == 201
    assert analysis['status'] == 'failed' and analysis['error_code'] == 'diagnostic_authorization_interrupted'
    assert '停止后续读取' in analysis['summary'] and '未连接服务器' not in analysis['summary']
    assert analysis['evidence']['grant_revision'] == '1'
    assert analysis['evidence']['execution_performed'] == ('True' if prior_evidence else 'unknown')
    assert all(analysis['evidence'][key] == value for key, value in prior_evidence.items())
    notification = database_session.scalar(select(AlertNotification).where(
        AlertNotification.analysis_id == analysis['id']))
    assert notification is not None and '停止后续读取' in notification.body
    timeline = client.get(f"/api/v1/alerts/{response.json()['id']}/timeline").json()
    final = next(item for item in timeline if item['event_type'] == 'diagnostic_finished')
    assert '停止后续读取' in final['note'] and '未执行诊断' not in final['note']
