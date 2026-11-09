from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.services import (
    alert_notification_delivery, diagnostic_job_claims, diagnostic_job_worker,
)
from test_diagnostic_jobs import authorized as authorized_fixture


@pytest.fixture
def authorized(client: TestClient, database_session: Session) -> dict[str, object]:
    return authorized_fixture.__wrapped__(client, database_session)


def test_signed_ingest_to_diagnosis_delivery_acknowledgement_and_rerun(
    client: TestClient, database_session: Session, authorized: dict[str, object],
) -> None:
    alert_id = authorized['id']
    endpoint = f'/api/v1/alerts/{alert_id}'
    assert client.patch('/api/v1/alert-notification-config', json={
        'enabled': True, 'host': 'smtp.example.test', 'security': 'ssl', 'port': 465,
        'sender': 'platform@example.test', 'recipients': ['admin@example.test'],
    }).status_code == 200
    claim = diagnostic_job_claims.claim_next(database_session)
    assert claim is not None
    current = client.get(endpoint).json()
    assert current['latest_analysis']['status'] == 'running'
    assert current['notification_summary']['latest_diagnostic'] is None
    with patch('src.services.alert_diagnostics.collect', return_value={
        'status': 'completed', 'summary': '只读证据，根因待人工确认',
        'evidence': {'read_only': 'true', 'remediation': 'disabled'},
    }) as collect:
        assert diagnostic_job_worker.execute_claim(
            client.app.state.session_factory, client.app.state.credential_vault, claim,
        )
    collect.assert_called_once()
    current = client.get(endpoint).json()
    notice = current['notification_summary']['latest_diagnostic']
    assert notice['analysis_id'] == claim.analysis_id and notice['status'] == 'pending'
    assert not notice['acknowledged']
    assert client.post(f"/api/v1/alert-notifications/{notice['id']}/acknowledge").status_code == 409
    with patch('src.services.alert_notification_delivery.send_smtp') as smtp:
        assert alert_notification_delivery.deliver_batch(
            client.app.state.session_factory, client.app.state.credential_vault,
        ) == 2
    assert smtp.call_count == 2
    delivered = client.get(endpoint).json()['notification_summary']['latest_diagnostic']
    assert delivered['status'] == 'sent' and not delivered['acknowledged']
    assert client.post(f"/api/v1/alert-notifications/{notice['id']}/acknowledge").status_code == 200
    confirmed = client.get(endpoint).json()
    assert confirmed['notification_summary']['latest_diagnostic']['acknowledged']
    assert confirmed['status'] == 'open' and confirmed['latest_recovery'] is None
    assert client.post(endpoint + '/diagnostics', json={
        'confirmation': alert_id, 'reason': '人工再次只读排查',
    }).status_code == 202
    next_claim = diagnostic_job_claims.claim_next(database_session)
    assert next_claim is not None and next_claim.analysis_id != claim.analysis_id
    rerun = client.get(endpoint).json()
    assert rerun['notification_summary']['diagnostic_results'] == 1
    assert rerun['notification_summary']['latest_diagnostic'] is None
    database_session.expire_all()
    assert database_session.get(Alert, alert_id).status == 'open'
